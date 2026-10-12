"""Opt-in local OpenTelemetry exporter and metadata-only provider spans.

Nothing is installed globally or enabled on import. Application wiring must
explicitly configure a tracer, request context and exporter lifecycle.
"""

import base64
import json
import logging
import time
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def exporter_settings(env):
    """Validate explicit local opt-in before loading optional dependencies.

    Returned headers contain credentials: keep this object in memory only.
    """
    enabled = env.get("MEM0_RUNTIME_TRACING", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("MEM0_RUNTIME_TRACING must be true or false")
    if enabled == "false":
        return None
    endpoint = env.get("MEM0_RUNTIME_TRACE_ENDPOINT", "")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid local runtime trace endpoint") from None
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost", "host.docker.internal", "langfuse", "langfuse-web"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path != "/api/public/otel/v1/traces" or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Runtime traces require an explicit local OTLP trace endpoint")
    public = env.get("LANGFUSE_PUBLIC_KEY", "")
    secret = env.get("LANGFUSE_SECRET_KEY", "")
    if (not public or not secret or any(char in public + secret for char in "\r\n")
            or ":" in public):
        raise ValueError("Runtime tracing requires valid Langfuse credentials")
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return endpoint, {"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"}


def build_exporter(env):
    """Return (tracer, owned provider) or None; no global OTel installation.

    Enabled configuration errors are explicit. Construction is not export/readback
    proof, and the application must flush/shut down the returned provider.
    """
    settings = exporter_settings(env)
    if settings is None:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        raise RuntimeError("Install the optional server tracing requirements before enabling runtime tracing") from None
    endpoint, headers = settings
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=5)
    provider = TracerProvider(resource=Resource.create({"service.name": "mem0-python-service"}), shutdown_on_exit=False)
    provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=256, max_export_batch_size=32,
                                                  schedule_delay_millis=1000, export_timeout_millis=5000))
    return provider.get_tracer("mem0.runtime"), provider


def _safe(action):
    try:
        return action()
    except Exception:
        # Never log exception text: exporter failures may include credentials.
        try:
            logger.warning("Runtime tracing operation failed; original provider behavior preserved")
        except Exception:
            pass
        return None


@contextmanager
def request_span(tracer, request_id, method, route):
    """Activate a metadata-only root span; never record raw exception messages.

    Route must be a fixed route template, not a URL containing user data.
    Caller supplies only server-generated request IDs.
    """
    span = None
    context = None
    if tracer is not None:
        span = _safe(lambda: tracer.start_span("mem0.http", attributes={
            "langfuse.observation.type": "span", "mem0.request_id": request_id,
            "http.request.method": method, "http.route": route,
        }))
        if span is not None:
            def activate():
                from opentelemetry.trace import use_span
                active = use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)
                active.__enter__()
                return active
            context = _safe(activate)
    try:
        yield span
    finally:
        if context is not None:
            _safe(lambda: context.__exit__(None, None, None))
        if span is not None:
            _safe(span.end)


def http_status(span, code):
    if span is not None:
        _safe(lambda: span.set_attribute("http.response.status_code", code))


def shutdown_exporter(provider):
    """Best-effort shutdown; a successful flush is not Langfuse readback proof."""
    if provider is None:
        return None
    flushed = _safe(lambda: provider.force_flush(timeout_millis=5000))
    _safe(provider.shutdown)
    return flushed is True


def attach_memory(memory, tracer, request_id):
    """Wrap per-instance provider methods once; no inference during attachment.

    Adapter and compatible chat SDK spans are not physical requests. Content,
    credentials, vectors and raw exception messages are never recorded. Other
    SDKs and server-phase timing require separate instrumentation.
    """
    for role, methods in (
        ("llm", ("generate_response",)),
        ("embedding_model", ("embed", "embed_batch")),
        ("reranker", ("rerank",)),
    ):
        provider = getattr(memory, role, None)
        for method in methods:
            original = getattr(provider, method, None)
            if not callable(original) or getattr(original, "_mem0_runtime_traced", False):
                continue
            setattr(provider, method, _wrap(original, tracer, request_id, role, method))
        if role == "llm":
            _safe(lambda: _attach_chat_sdk(provider, tracer, request_id))
    return memory


def _attach_chat_sdk(provider, tracer, request_id):
    client = getattr(provider, "client", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    original = getattr(completions, "create", None)
    if callable(original) and not getattr(original, "_mem0_runtime_traced", False):
        completions.create = _wrap(original, tracer, request_id, "llm_sdk", "chat.completions.create", sdk=True)


def _response_metadata(span, response):
    """Only allowlisted scalar response metadata; never serialize raw output."""
    model = getattr(response, "model", None)
    if isinstance(model, str):
        span.set_attribute("langfuse.observation.model.name", model[:512])
    usage = getattr(response, "usage", None)
    details = {}
    for source, target in (("prompt_tokens", "input"), ("completion_tokens", "output"), ("total_tokens", "total")):
        value = getattr(usage, source, None)
        if type(value) is int and value >= 0:
            details[target] = value
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None)
    for name, value in (("input_cached", cached), ("output_reasoning", reasoning)):
        if type(value) is int and value >= 0:
            details[name] = value
    if details:
        span.set_attribute("langfuse.observation.usage_details", json.dumps(details, sort_keys=True))
    choices = getattr(response, "choices", None)
    if isinstance(choices, (list, tuple)) and choices:
        reason = getattr(choices[0], "finish_reason", None)
        if reason in {"stop", "length", "tool_calls", "content_filter", "function_call"}:
            span.set_attribute("mem0.finish_reason", reason)


def _activate_provider_span(span):
    if span is None or not callable(getattr(span, "get_span_context", None)):
        return None
    from opentelemetry.trace import use_span
    active = use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)
    active.__enter__()
    return active


def _wrap(original, tracer, request_id, role, method, sdk=False):
    @wraps(original)
    def call(*args, **kwargs):
        started = time.perf_counter()

        def start():
            rid = request_id()
            attributes = {
                "langfuse.observation.type": "generation" if sdk else "span",
                "mem0.provider.role": role,
                "mem0.provider.method": method,
                "mem0.attempt_scope": "sdk_invocation_not_physical_attempt" if sdk else "adapter_invocation_not_physical_attempt",
                "mem0.request_id": rid if isinstance(rid, str) and len(rid) <= 32 else "unknown",
            }
            return tracer.start_span(f"mem0.{role}.{method}", attributes=attributes)

        span = _safe(start)
        context = _safe(lambda: _activate_provider_span(span))
        outcome = "returned"
        error_type = None
        try:
            response = original(*args, **kwargs)
            if sdk and span is not None:
                _safe(lambda: _response_metadata(span, response))
            return response
        except BaseException as exc:
            outcome = "raised"
            error_type = type(exc).__name__
            raise
        finally:
            if context is not None:
                _safe(lambda: context.__exit__(None, None, None))
            if span is not None:
                def finish():
                    span.set_attribute("mem0.outcome", outcome)
                    span.set_attribute("mem0.elapsed_ms", (time.perf_counter() - started) * 1000)
                    if error_type is not None:
                        span.set_attribute("mem0.error_type", error_type)

                _safe(finish)
                _safe(span.end)

    call._mem0_runtime_traced = True
    return call
