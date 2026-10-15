import asyncio

import pytest

import mem0.runtime_tracing as runtime_tracing


class _Span:
    def __init__(self):
        self.attributes = {}
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


class _Tracer:
    def __init__(self, fail=False):
        self.span = _Span()
        self.fail = fail

    def start_span(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("trace failure")
        return self.span


class _Runtime:
    def __init__(self, tracer):
        self.tracer = tracer


def test_trace_call_preserves_result_when_tracing_fails(monkeypatch):
    monkeypatch.setattr(runtime_tracing, "_runtime", _Runtime(_Tracer(fail=True)))

    @runtime_tracing.trace_call("test", "llm", "generation")
    def infer(value):
        return value + 1

    assert infer(4) == 5


def test_trace_call_preserves_provider_exception(monkeypatch):
    tracer = _Tracer()
    monkeypatch.setattr(runtime_tracing, "_runtime", _Runtime(tracer))

    @runtime_tracing.trace_call("test", "llm", "generation")
    def infer():
        raise LookupError("provider failure")

    with pytest.raises(LookupError, match="provider failure"):
        infer()
    assert tracer.span.ended
    assert tracer.span.attributes["mem0.error_type"] == "LookupError"
    assert "provider failure" not in repr(tracer.span.attributes)


def test_async_calls_keep_concurrent_parent_contexts_isolated(monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.concurrent")
    monkeypatch.setattr(runtime_tracing, "_runtime", runtime_tracing.TracingRuntime(tracer))
    released = asyncio.Event()
    seen = []

    @runtime_tracing.trace_call("infer", "embedding", "embedding")
    async def infer(value):
        seen.append(trace.get_current_span().get_span_context().span_id)
        if len(seen) == 2:
            released.set()
        await released.wait()
        return value

    async def operation(value):
        with tracer.start_as_current_span(f"parent-{value}") as parent:
            pending = infer(value)
            assert trace.get_current_span() is parent
            assert await pending == value
            assert trace.get_current_span() is parent

    async def run():
        await asyncio.gather(operation(1), operation(2))

    try:
        assert asyncio.iscoroutinefunction(infer)
        asyncio.run(run())
        spans = exporter.get_finished_spans()
        parents = {span.context.span_id: span for span in spans if span.name.startswith("parent-")}
        children = [span for span in spans if span.name == "mem0.embedding.infer"]
        assert {span.parent.span_id for span in children} == set(parents)
        assert {span.context.span_id for span in children} == set(seen)
        assert trace.get_current_span().get_span_context().span_id == 0
    finally:
        provider.shutdown()


def test_flush_detaches_runtime_before_later_inference(monkeypatch):
    from types import SimpleNamespace

    calls = []
    owned = SimpleNamespace(
        force_flush=lambda **kwargs: calls.append("flush") or True, shutdown=lambda: calls.append("shutdown")
    )
    runtime = runtime_tracing.TracingRuntime(_Tracer(), owned)
    monkeypatch.setattr(runtime_tracing, "_runtime", runtime)
    monkeypatch.delenv("MEM0_RUNTIME_TRACING", raising=False)
    assert runtime_tracing.flush_tracing()
    assert calls == ["flush", "shutdown"]
    assert runtime_tracing.current_tracer() is None


def test_application_tracer_is_not_shutdown_by_sdk(monkeypatch):
    monkeypatch.setattr(runtime_tracing, "_runtime", None)
    tracer = _Tracer()
    runtime_tracing.set_runtime_tracer(tracer, lambda: "abc123")
    with pytest.raises(RuntimeError, match="already configured"):
        runtime_tracing.set_runtime_tracer(_Tracer())
    assert runtime_tracing.flush_tracing()
    assert not tracer.span.ended


def test_decorated_async_sdk_tracks_completion_usage_without_duplicate_generation(monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    from openai.resources.chat.completions import AsyncCompletions
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(runtime_tracing, "_runtime", runtime_tracing.TracingRuntime(provider.get_tracer("test.sdk")))
    monkeypatch.setattr(
        AsyncCompletions,
        "create",
        runtime_tracing._raw_sdk_call(AsyncCompletions.create, "sdk_completion", "generation"),
    )

    async def respond(request):
        assert trace.get_current_span().is_recording()
        return httpx.Response(
            200,
            json={
                "id": "completion-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "private-output"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            },
        )

    @runtime_tracing.trace_call("adapter", "llm", "generation")
    async def adapter(client):
        response = await client.chat.completions.create(
            model="test-model", messages=[{"role": "user", "content": "private-input"}]
        )
        return response.choices[0].message.content

    async def run():
        async with AsyncOpenAI(
            api_key="test-key",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            await client.chat.completions.create(
                model="test-model", messages=[{"role": "user", "content": "private-input"}]
            )
            assert await adapter(client) == "private-output"

    try:
        asyncio.run(run())
        spans = exporter.get_finished_spans()
        assert {span.name for span in spans} == {"mem0.sdk.sdk_completion", "mem0.llm.adapter"}
        assert len(spans) == 2
        for span in spans:
            assert span.attributes["langfuse.observation.model.name"] == "test-model"
            assert span.attributes["langfuse.observation.usage_details"] == '{"input": 7, "output": 2, "total": 9}'
            assert "private-input" not in repr(span.attributes)
            assert "private-output" not in repr(span.attributes)
    finally:
        provider.shutdown()


@pytest.mark.parametrize("complete", [True, False])
def test_sdk_stream_owns_span_until_consumed_or_closed(monkeypatch, complete):
    import json

    import httpx
    from openai import AsyncOpenAI, AsyncStream
    from openai.resources.chat.completions import AsyncCompletions
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(runtime_tracing, "_runtime", runtime_tracing.TracingRuntime(provider.get_tracer("test.stream")))
    monkeypatch.setattr(
        AsyncCompletions,
        "create",
        runtime_tracing._raw_sdk_call(AsyncCompletions.create, "sdk_stream", "generation"),
    )
    chunks = [
        {
            "id": "stream-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"content": "private-output"}, "finish_reason": None}],
        },
        {
            "id": "stream-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        },
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"

    async def run():
        async with AsyncOpenAI(
            api_key="test-key",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"}),
                )
            ),
        ) as client:
            stream = await client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "private-input"}],
                stream=True,
            )
            assert isinstance(stream, AsyncStream)
            assert not exporter.get_finished_spans()
            assert trace.get_current_span().get_span_context().span_id == 0
            first = await stream.__anext__()
            assert first.choices[0].delta.content == "private-output"
            assert not exporter.get_finished_spans()
            if complete:
                async for _chunk in stream:
                    pass
            await stream.close()

    try:
        asyncio.run(run())
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes["mem0.outcome"] == ("returned" if complete else "cancelled")
        if complete:
            assert spans[0].attributes["langfuse.observation.usage_details"] == '{"input": 7, "output": 2, "total": 9}'
        assert "private-input" not in repr(spans[0].attributes)
        assert "private-output" not in repr(spans[0].attributes)
    finally:
        provider.shutdown()
