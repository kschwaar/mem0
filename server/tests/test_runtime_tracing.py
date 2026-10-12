"""Provider behavior tests, no models, exporters or service startup."""

import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("runtime_tracing", Path(__file__).parents[1] / "runtime_tracing.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Span:
    def __init__(self, attributes):
        self.attributes = dict(attributes)
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


class Tracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes):
        span = Span(attributes)
        self.spans.append((name, span))
        return span


def test_attachment_is_once_and_preserves_identity_without_content():
    result = object()
    calls = []
    provider = SimpleNamespace(generate_response=lambda *args, **kwargs: calls.append((args, kwargs)) or result)
    memory = SimpleNamespace(llm=provider)
    tracer = Tracer()
    module.attach_memory(memory, tracer, lambda: "abcdefgh")
    module.attach_memory(memory, tracer, lambda: "abcdefgh")
    assert calls == []
    assert provider.generate_response("private prompt", api_key="private credential") is result
    assert len(calls) == 1 and len(tracer.spans) == 1
    name, span = tracer.spans[0]
    assert name == "mem0.llm.generate_response" and span.ended
    assert span.attributes["mem0.request_id"] == "abcdefgh"
    assert "private" not in repr(span.attributes)


@pytest.mark.parametrize("failure", ["start", "attributes", "end"])
def test_tracing_failure_does_not_change_provider_result(failure):
    class Broken(Tracer):
        def start_span(self, name, attributes):
            if failure == "start":
                raise RuntimeError("private exporter credential")
            span = super().start_span(name, attributes)
            def fail(*_args):
                raise RuntimeError("private exporter credential")
            if failure == "attributes":
                span.set_attribute = fail
            if failure == "end":
                span.end = fail
            return span

    result = object()
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=lambda: result))
    module.attach_memory(memory, Broken(), lambda: "request")
    assert memory.llm.generate_response() is result


def test_original_exception_preserved_and_request_id_read_per_call():
    exception = ValueError("private provider error")
    def fail():
        raise exception
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=fail))
    tracer = Tracer()
    ids = iter(["first", "second"])
    module.attach_memory(memory, tracer, lambda: next(ids))
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            memory.llm.generate_response()
        assert caught.value is exception
    assert [span.attributes["mem0.request_id"] for _, span in tracer.spans] == ["first", "second"]
    assert all(span.ended and span.attributes["mem0.error_type"] == "ValueError" for _, span in tracer.spans)
    assert "private" not in repr([span.attributes for _, span in tracer.spans])


def test_failed_warning_sink_cannot_replace_provider_result(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("unavailable diagnostic sink")
    monkeypatch.setattr(module.logger, "warning", fail)
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=lambda: "original"))
    module.attach_memory(memory, SimpleNamespace(start_span=fail), lambda: "request")
    assert memory.llm.generate_response() == "original"


def load_state(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    state_spec = importlib.util.spec_from_file_location("isolated_tracing_state", Path(__file__).parents[1] / "server_state.py")
    state = importlib.util.module_from_spec(state_spec)
    state_spec.loader.exec_module(state)
    instances = []
    def factory(config):
        instance = SimpleNamespace(llm=SimpleNamespace(generate_response=lambda: config["llm"]["model"]))
        instances.append(instance)
        return instance
    # Memory SDK is the service's external boundary; do not call real providers.
    monkeypatch.setattr(state, "Memory", SimpleNamespace(from_config=factory))
    return state, instances


def test_service_tracing_survives_configuration_replacement(monkeypatch):
    state, instances = load_state(monkeypatch)
    tracer = Tracer()
    state.set_runtime_tracer(tracer, lambda: "request")
    state.initialize_state({"llm": {"model": "first"}})
    assert state.get_memory_instance().llm.generate_response() == "first"
    state.update_config({"llm": {"model": "second"}})
    assert state.get_memory_instance().llm.generate_response() == "second"
    assert len(instances) == 2 and instances[0] is not instances[1]
    assert len(tracer.spans) == 2 and all(span.ended for _, span in tracer.spans)
    with pytest.raises(RuntimeError):
        state.set_runtime_tracer(Tracer(), lambda: "different")


def test_service_default_has_no_tracing_side_effect(monkeypatch):
    state, instances = load_state(monkeypatch)
    state.initialize_state({"llm": {"model": "first"}})
    state.update_config({"llm": {"model": "second"}})
    assert len(instances) == 2
    assert not getattr(state.get_memory_instance().llm.generate_response, "_mem0_runtime_traced", False)
    assert state.get_memory_instance().llm.generate_response() == "second"


def test_overlapping_provider_calls_keep_request_context_separate():
    request_id = ContextVar("test_runtime_request", default="-")
    barrier = threading.Barrier(2)
    def provider():
        barrier.wait(timeout=3)
        return request_id.get()
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=provider))
    tracer = Tracer()
    module.attach_memory(memory, tracer, request_id.get)
    def invoke(rid):
        token = request_id.set(rid)
        try:
            return memory.llm.generate_response()
        finally:
            request_id.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, "first-request")
        second = pool.submit(invoke, "second-request")
        assert first.result(timeout=5) == "first-request"
        assert second.result(timeout=5) == "second-request"
    assert sorted(span.attributes["mem0.request_id"] for _, span in tracer.spans) == ["first-request", "second-request"]
    assert request_id.get() == "-"


def test_disabled_exporter_needs_no_credentials_or_optional_dependencies():
    assert module.build_exporter({}) is None
    assert module.build_exporter({"MEM0_RUNTIME_TRACING": "false"}) is None


@pytest.mark.parametrize("endpoint", [
    "https://example.com/api/public/otel/v1/traces",
    "http://user:password@localhost:4000/api/public/otel/v1/traces",
    "http://localhost:4000/api/public/otel/v1/traces?secret=x",
    "http://localhost:4000/api/public/otel/v1/traces#fragment",
    "http://localhost:4000/api/public/otel", "http://localhost:99999/api/public/otel/v1/traces",
])
def test_exporter_rejects_remote_or_ambiguous_targets(endpoint):
    with pytest.raises(ValueError):
        module.exporter_settings({"MEM0_RUNTIME_TRACING": "true", "MEM0_RUNTIME_TRACE_ENDPOINT": endpoint,
                                  "LANGFUSE_PUBLIC_KEY": "fixture-public", "LANGFUSE_SECRET_KEY": "fixture-secret"})


def test_explicit_local_exporter_settings_and_no_secret_in_errors():
    endpoint = "http://host.docker.internal:4000/api/public/otel/v1/traces"
    env = {"MEM0_RUNTIME_TRACING": "true", "MEM0_RUNTIME_TRACE_ENDPOINT": endpoint,
           "LANGFUSE_PUBLIC_KEY": "fixture-public", "LANGFUSE_SECRET_KEY": "fixture-secret"}
    target, headers = module.exporter_settings(env)
    assert target == endpoint and headers["Authorization"].startswith("Basic ")
    assert headers["x-langfuse-ingestion-version"] == "4"
    env["LANGFUSE_SECRET_KEY"] = "private\nsecret"
    with pytest.raises(ValueError) as caught:
        module.exporter_settings(env)
    assert "private" not in str(caught.value)


def test_real_sdk_request_parentage_and_exception_privacy():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import get_current_span
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.mem0.runtime")
    exception = ValueError("sensitive prompt or credential")
    def fail():
        raise exception
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=fail))
    module.attach_memory(memory, tracer, lambda: "abcdef12")
    try:
        with pytest.raises(ValueError) as caught:
            with module.request_span(tracer, "abcdef12", "POST", "/memories") as root:
                module.http_status(root, 502)
                memory.llm.generate_response()
        assert caught.value is exception
        spans = exporter.get_finished_spans()
        child = next(span for span in spans if span.name == "mem0.llm.generate_response")
        root = next(span for span in spans if span.name == "mem0.http")
        assert child.parent.span_id == root.context.span_id
        assert child.context.trace_id == root.context.trace_id
        assert root.attributes["http.response.status_code"] == 502
        assert not root.events and not child.events
        assert "sensitive" not in repr([dict(span.attributes) for span in spans])
        assert not get_current_span().get_span_context().is_valid
    finally:
        provider.shutdown()


def test_real_otlp_factory_constructs_without_dispatch():
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter",
                        reason="Optional HTTP exporter; execute with permanent tracing-deps on PYTHONPATH")
    built = module.build_exporter({"MEM0_RUNTIME_TRACING": "true",
        "MEM0_RUNTIME_TRACE_ENDPOINT": "http://127.0.0.1:1/api/public/otel/v1/traces",
        "LANGFUSE_PUBLIC_KEY": "fixture-public", "LANGFUSE_SECRET_KEY": "fixture-secret"})
    tracer, provider = built
    # No spans emitted: this exercises actual SDK/exporter construction, not transport.
    assert callable(tracer.start_span)
    assert provider.force_flush(timeout_millis=1000)
    provider.shutdown()


def test_actual_openai_adapter_sdk_metadata_and_nested_spans(monkeypatch):
    from openai.types.chat import ChatCompletion
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from mem0.configs.llms.openai import OpenAIConfig
    from mem0.llms.openai import OpenAILLM

    response = ChatCompletion(id="fixture-id", object="chat.completion", created=1, model="fixture-response-model",
        choices=[{"index": 0, "finish_reason": "length", "message": {
            "role": "assistant", "content": '{"facts":["private fixture fact"]}',
            "reasoning_content": "private reasoning"}}],
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
               "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 1}})
    llm = OpenAILLM(OpenAIConfig(model="fixture-request-model", api_key="fixture-key"))
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.actual-adapter")
    memory = SimpleNamespace(llm=llm)
    module.attach_memory(memory, tracer, lambda: "abcdef12")
    module.attach_memory(memory, tracer, lambda: "abcdef12")
    try:
        with module.request_span(tracer, "abcdef12", "POST", "/memories"):
            assert llm.generate_response([{"role": "user", "content": "private prompt"}]) == response.choices[0].message.content
        assert len(calls) == 1
        spans = {span.name: span for span in exporter.get_finished_spans()}
        sdk = spans["mem0.llm_sdk.chat.completions.create"]
        adapter = spans["mem0.llm.generate_response"]
        assert sdk.parent.span_id == adapter.context.span_id
        assert adapter.parent.span_id == spans["mem0.http"].context.span_id
        assert sdk.attributes["langfuse.observation.model.name"] == "fixture-response-model"
        assert sdk.attributes["mem0.finish_reason"] == "length"
        assert sdk.attributes["langfuse.observation.type"] == "generation"
        import json
        assert json.loads(sdk.attributes["langfuse.observation.usage_details"]) == {
            "input": 7, "output": 3, "total": 10, "input_cached": 4, "output_reasoning": 1}
        assert "private" not in repr([dict(span.attributes) for span in spans.values()])
    finally:
        provider.shutdown()
        llm.client.close()
