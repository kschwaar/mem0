"""Opt-in, metadata-only OpenTelemetry helpers for Python inference boundaries.

Tracing is inert until :func:`configure_tracing` is called. It creates an owned
provider and never installs one globally. No prompts, outputs, vectors,
credentials, or exception messages are added to spans.
"""

import asyncio
import atexit
import base64
import functools
import importlib
import inspect
import json
import logging
import os
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_runtime = None
_runtime_lock = threading.RLock()
_inference_span = ContextVar("mem0_inference_span", default=None)


@dataclass
class TracingRuntime:
    """An explicitly configured tracer and the provider that owns its exporter."""

    tracer: object
    provider: object = None
    request_id: object = None

    def flush(self, timeout_millis=5000):
        """Flush and shut down an owned exporter, never an application's provider."""
        if self.provider is None:
            return True
        flushed = _safe(lambda: self.provider.force_flush(timeout_millis=timeout_millis))
        _safe(self.provider.shutdown)
        return flushed is True


def set_runtime_tracer(tracer, request_id=None):
    """Use an application-owned tracer for every SDK inference boundary."""
    global _runtime
    if not callable(getattr(tracer, "start_span", None)):
        raise ValueError("Runtime tracing requires a tracer")
    if request_id is not None and not callable(request_id):
        raise ValueError("Request ID getter must be callable")
    with _runtime_lock:
        if _runtime is not None and _runtime.tracer is not tracer:
            raise RuntimeError("Runtime tracer already configured")
        _runtime = TracingRuntime(tracer, request_id=request_id)
    return _runtime


def configure_tracing(env=None):
    """Configure local Langfuse export; explicit configuration errors are raised.

    Opt in with ``MEM0_RUNTIME_TRACING=true`` and project keys. ``LANGFUSE_BASE_URL``
    defaults to ``http://localhost:4000``. Repeated calls reuse the owned runtime.
    Lazy inference setup is best-effort; explicit callers can detect missing setup.
    """
    global _runtime
    env = os.environ if env is None else env
    enabled = env.get("MEM0_RUNTIME_TRACING", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("MEM0_RUNTIME_TRACING must be true or false")
    if enabled == "false":
        return None
    with _runtime_lock:
        if _runtime is not None:
            return _runtime
        base_url = env.get("LANGFUSE_BASE_URL", "http://localhost:4000")
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname
                not in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "langfuse", "langfuse-web"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise ValueError()
        except ValueError:
            raise ValueError("Runtime tracing requires a local Langfuse base URL") from None
        public, secret = env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", "")
        if not public or not secret or any(c in public + secret for c in "\r\n") or ":" in public:
            raise ValueError("Runtime tracing requires valid Langfuse credentials")
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            raise RuntimeError("Install mem0ai[tracing] before enabling runtime tracing") from None

        endpoint = f"{base_url.rstrip('/')}/api/public/otel/v1/traces"
        headers = {
            "Authorization": "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode(),
            "x-langfuse-ingestion-version": "4",
        }
        exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=5)
        provider = TracerProvider(resource=Resource.create({"service.name": "mem0-python-sdk"}), shutdown_on_exit=False)
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=256,
                max_export_batch_size=32,
                schedule_delay_millis=1000,
                export_timeout_millis=5000,
            )
        )
        _runtime = TracingRuntime(provider.get_tracer("mem0.runtime"), provider)
        instrument_sdk_calls()
        return _runtime


def current_tracer():
    """Return the configured tracer, lazily honoring explicit environment opt-in."""
    if _runtime is None and os.environ.get("MEM0_RUNTIME_TRACING") == "true":
        _safe(configure_tracing)
    return _runtime.tracer if _runtime is not None else None


def flush_tracing(runtime=None, timeout_millis=5000):
    """Flush and shut down an owned runtime; subsequent calls may configure anew."""
    global _runtime
    with _runtime_lock:
        runtime = runtime or _runtime
        if runtime is _runtime:
            _runtime = None
    return runtime.flush(timeout_millis) if runtime is not None else True


def trace_call(name, role="inference", kind="span"):
    """Decorator tracing one logical adapter/provider invocation.

    It handles synchronous and asynchronous callables and preserves their
    return values and exceptions. ``kind='generation'`` marks Langfuse
    generations; SDK retries remain within this logical invocation span.
    """

    def decorate(function):
        if getattr(function, "_mem0_runtime_traced", False):
            return function

        if asyncio.iscoroutinefunction(inspect.unwrap(function)):

            @functools.wraps(function)
            async def invoke(*args, **kwargs):
                tracer = current_tracer()
                if tracer is None:
                    return await function(*args, **kwargs)
                return await _run_async(tracer, function, args, kwargs, name, role, kind)
        else:

            @functools.wraps(function)
            def invoke(*args, **kwargs):
                tracer = current_tracer()
                if tracer is None:
                    return function(*args, **kwargs)
                return _run_sync(tracer, function, args, kwargs, name, role, kind)

        invoke._mem0_runtime_traced = True
        return invoke

    return decorate


def _attributes(role, name, kind, args):
    attributes = {
        "langfuse.observation.type": kind,
        "mem0.provider.role": role,
        "mem0.provider.method": name,
        "mem0.attempt_scope": "logical_provider_invocation",
    }
    if args and role in {"llm", "embedding", "reranker"}:
        owner = args[0]
        attributes["mem0.provider.class"] = type(owner).__name__
        config = getattr(owner, "config", None)
        model = getattr(config, "model", None) or getattr(owner, "model", None)
        if isinstance(model, str):
            attributes["langfuse.observation.model.name"] = model[:512]
    getter = getattr(_runtime, "request_id", None)
    if getter is not None:
        rid = getter()
        if isinstance(rid, str) and len(rid) <= 32:
            attributes["mem0.request_id"] = rid
    return attributes


def _run_sync(tracer, function, args, kwargs, name, role, kind):
    span = _safe(lambda: tracer.start_span(f"mem0.{role}.{name}", attributes=_attributes(role, name, kind, args)))
    active = _activate(span)
    started = time.perf_counter()
    outcome, error_type = "returned", None
    token = _inference_span.set(span) if span is not None and kind in {"generation", "embedding"} else None
    streaming = False
    try:
        if args and role in {"llm", "embedding", "reranker"}:
            _safe(lambda: _instrument_sdk_metadata(args[0]))
        result = function(*args, **kwargs)
        if span is not None:
            _safe(lambda: _response_metadata(span, result))
            streaming = bool(_safe(lambda: _trace_stream(span, result, started)))
        return result
    except BaseException as exc:
        outcome, error_type = "raised", type(exc).__name__
        raise
    finally:
        if streaming:
            if active is not None:
                _safe(lambda: active.__exit__(None, None, None))
        else:
            _finish(span, active, started, outcome, error_type)
        if token is not None:
            _inference_span.reset(token)


async def _run_async(tracer, function, args, kwargs, name, role, kind):
    span = _safe(lambda: tracer.start_span(f"mem0.{role}.{name}", attributes=_attributes(role, name, kind, args)))
    active = _activate(span)
    started = time.perf_counter()
    outcome, error_type = "returned", None
    token = _inference_span.set(span) if span is not None and kind in {"generation", "embedding"} else None
    streaming = False
    try:
        if args and role in {"llm", "embedding", "reranker"}:
            _safe(lambda: _instrument_sdk_metadata(args[0]))
        result = await function(*args, **kwargs)
        if span is not None:
            _safe(lambda: _response_metadata(span, result))
            streaming = bool(_safe(lambda: _trace_stream(span, result, started)))
        return result
    except BaseException as exc:
        outcome, error_type = "raised", type(exc).__name__
        raise
    finally:
        if streaming:
            if active is not None:
                _safe(lambda: active.__exit__(None, None, None))
        else:
            _finish(span, active, started, outcome, error_type)
        if token is not None:
            _inference_span.reset(token)


def _trace_stream(span, result, started):
    """Keep OpenAI stream identity; completion/close owns the deferred span.

    OpenAI's Stream/AsyncStream delegate iteration to `_iterator`. Restrict this
    SDK-specific hook to those classes rather than modifying arbitrary results.
    """
    from openai import AsyncStream, Stream

    if not isinstance(result, (Stream, AsyncStream)):
        return False
    source, original_close = result._iterator, result.close
    ended = False

    def finish(outcome, error_type=None):
        nonlocal ended
        if ended:
            return
        ended = True
        _finish(span, None, started, outcome, error_type)

    if isinstance(result, AsyncStream):

        async def iterate():
            outcome, error_type = "returned", None
            try:
                async for item in source:
                    _safe(lambda: _response_metadata(span, item))
                    yield item
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, (GeneratorExit, asyncio.CancelledError)) else "raised"
                error_type = type(exc).__name__ if outcome == "raised" else None
                raise
            finally:
                finish(outcome, error_type)

        async def close():
            try:
                return await original_close()
            except BaseException as exc:
                finish("raised", type(exc).__name__)
                raise
            finally:
                finish("cancelled")
    else:

        def iterate():
            outcome, error_type = "returned", None
            try:
                for item in source:
                    _safe(lambda: _response_metadata(span, item))
                    yield item
            except BaseException as exc:
                outcome = "cancelled" if isinstance(exc, GeneratorExit) else "raised"
                error_type = type(exc).__name__ if outcome == "raised" else None
                raise
            finally:
                finish(outcome, error_type)

        def close():
            try:
                return original_close()
            except BaseException as exc:
                finish("raised", type(exc).__name__)
                raise
            finally:
                finish("cancelled")

    result._iterator = iterate()
    result.close = close
    return True


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _response_metadata(span, response):
    """Allowlisted usage/model only; never consume streams or serialize responses."""
    model = _field(response, "model") or _field(response, "model_version")
    if isinstance(model, str):
        span.set_attribute("langfuse.observation.model.name", model[:512])
    usage = _field(response, "usage") or _field(response, "usage_metadata")
    details = {}
    for target, fields in (
        ("input", ("prompt_tokens", "input_tokens", "prompt_token_count", "input_tokens_total")),
        ("output", ("completion_tokens", "output_tokens", "candidates_token_count", "output_tokens_total")),
        ("total", ("total_tokens", "total_token_count")),
    ):
        for source in fields:
            value = _field(usage, source)
            if type(value) is int and value >= 0:
                details[target] = value
                break
    for target, source in (("input", "prompt_eval_count"), ("output", "eval_count")):
        value = _field(response, source)
        if type(value) is int and value >= 0:
            details[target] = value
    for target, group, key in (
        ("input_cached", "prompt_tokens_details", "cached_tokens"),
        ("output_reasoning", "completion_tokens_details", "reasoning_tokens"),
    ):
        value = _field(_field(usage, group), key)
        if type(value) is int and value >= 0:
            details[target] = value
    if details:
        span.set_attribute("langfuse.observation.usage_details", json.dumps(details, sort_keys=True))
    choices = _field(response, "choices")
    reason = _field(choices[0], "finish_reason") if isinstance(choices, (list, tuple)) and choices else None
    reason = reason or _field(response, "stop_reason")
    if isinstance(reason, str) and reason in {
        "stop",
        "length",
        "tool_calls",
        "content_filter",
        "function_call",
        "end_turn",
        "max_tokens",
        "tool_use",
        "stop_sequence",
    }:
        span.set_attribute("mem0.finish_reason", reason)


def _metadata_call(function):
    def record(response):
        from opentelemetry.trace import get_current_span

        span = get_current_span()
        if span.is_recording():
            _response_metadata(span, response)

    if asyncio.iscoroutinefunction(inspect.unwrap(function)):

        @functools.wraps(function)
        async def invoke(*args, **kwargs):
            response = await function(*args, **kwargs)
            _safe(lambda: record(response))
            return response
    else:

        @functools.wraps(function)
        def invoke(*args, **kwargs):
            response = function(*args, **kwargs)
            _safe(lambda: record(response))
            return response

    invoke._mem0_metadata_traced = True
    return invoke


def _instrument_sdk_metadata(provider):
    """Enrich the active adapter span, never create a second generation."""
    if getattr(provider, "_mem0_sdk_metadata_attached", False):
        return
    client = getattr(provider, "client", None)
    paths = (
        ("chat", "completions", "create"),
        ("beta", "chat", "completions", "parse"),
        ("messages", "create"),
        ("embeddings", "create"),
        ("models", "generate_content"),
        ("models", "embed_content"),
        ("chat",),
        ("embed",),
        ("rerank",),
    )
    for path in paths:
        owner = client
        for part in path[:-1]:
            owner = getattr(owner, part, None)
        function = getattr(owner, path[-1], None)
        if (
            callable(function)
            and not getattr(function, "_mem0_metadata_traced", False)
            and not getattr(function, "_mem0_runtime_traced", False)
        ):
            setattr(owner, path[-1], _metadata_call(function))
    provider._mem0_sdk_metadata_attached = True


def instrument_sdk_calls():
    """Instrument supported raw SDK boundaries, including external benchmark callers.

    Explicit setup only; no global OTEL provider installation. Nested Mem0 provider
    calls enrich their active observation instead of duplicating generations.
    """
    if _runtime is None:
        return
    for module_name, classes, method, kind in (
        ("openai.resources.chat.completions", ("Completions", "AsyncCompletions"), "create", "generation"),
        ("openai.resources.responses", ("Responses", "AsyncResponses"), "create", "generation"),
        ("openai.resources.embeddings", ("Embeddings", "AsyncEmbeddings"), "create", "embedding"),
        ("openai.resources.audio.transcriptions", ("Transcriptions", "AsyncTranscriptions"), "create", "generation"),
        ("anthropic.resources.messages", ("Messages", "AsyncMessages"), "create", "generation"),
        ("google.genai.models", ("Models", "AsyncModels"), "generate_content", "generation"),
        ("google.genai.models", ("Models", "AsyncModels"), "embed_content", "embedding"),
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for class_name in classes:
            owner = getattr(module, class_name, None)
            function = getattr(owner, method, None)
            if callable(function) and not getattr(function, "_mem0_runtime_traced", False):
                _safe(lambda: setattr(owner, method, _raw_sdk_call(function, f"{module_name}.{method}", kind)))


def _raw_sdk_call(function, name, kind):
    traced = trace_call(name, "sdk", kind)(function)
    metadata = _metadata_call(function)
    if asyncio.iscoroutinefunction(inspect.unwrap(function)):

        @functools.wraps(function)
        async def invoke(*args, **kwargs):
            if _inference_span.get() is not None:
                return await metadata(*args, **kwargs)
            return await traced(*args, **kwargs)
    else:

        @functools.wraps(function)
        def invoke(*args, **kwargs):
            if _inference_span.get() is not None:
                return metadata(*args, **kwargs)
            return traced(*args, **kwargs)

    invoke._mem0_runtime_traced = True
    return invoke


def _activate(span):
    if span is None:
        return None

    def activate():
        from opentelemetry.trace import use_span

        context = use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)
        context.__enter__()
        return context

    return _safe(activate)


def _finish(span, active, started, outcome, error_type):
    if active is not None:
        _safe(lambda: active.__exit__(None, None, None))
    if span is not None:
        _safe(lambda: span.set_attribute("mem0.outcome", outcome))
        _safe(lambda: span.set_attribute("mem0.elapsed_ms", (time.perf_counter() - started) * 1000))
        if error_type:
            _safe(lambda: span.set_attribute("mem0.error_type", error_type))
        if error_type:

            def mark_error():
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR))

            _safe(mark_error)
        _safe(span.end)


def _safe(action):
    try:
        return action()
    except Exception:
        try:
            logger.warning("Runtime tracing operation failed; inference behavior preserved")
        except Exception:
            pass
        return None


atexit.register(flush_tracing)
