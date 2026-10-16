"""HTTP spans preserve context and do not export provider error messages."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from runtime_tracing import http_status, request_span


def test_http_span_parents_inference_and_restores_context_after_error():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.http")
    failure = ValueError("private-provider-response-and-credential")
    try:
        with tracer.start_as_current_span("caller") as caller:
            caller_id = caller.get_span_context().span_id
            with pytest.raises(ValueError) as caught:
                with request_span(tracer, "mem0_test", "POST", "/memories/{memory_id}") as root:
                    root_id = root.get_span_context().span_id
                    with tracer.start_as_current_span(
                        "inference", record_exception=False, set_status_on_exception=False
                    ) as inference:
                        assert inference.parent.span_id == root_id
                    http_status(root, 503)
                    raise failure
            assert caught.value is failure
            assert trace.get_current_span().get_span_context().span_id == caller_id
        spans = exporter.get_finished_spans()
        root = next(span for span in spans if span.context.span_id == root_id)
        assert root.parent.span_id == caller_id
        assert root.attributes["http.route"] == "/memories/{memory_id}"
        assert root.attributes["http.response.status_code"] == 503
        assert not root.events
        assert "private-provider-response-and-credential" not in repr(root.attributes)
        assert not root.status.description
    finally:
        provider.shutdown()


def test_failed_tracer_does_not_replace_request_error_or_log_secret(caplog):
    secret = "private-exporter-credential"

    class BrokenTracer:
        def start_span(self, *args, **kwargs):
            raise RuntimeError(secret)

    before = trace.get_current_span().get_span_context()
    failure = LookupError("request failed")
    with pytest.raises(LookupError) as caught:
        with request_span(BrokenTracer(), "mem0_test", "GET", "/memories") as root:
            assert root is None
            raise failure
    assert caught.value is failure
    assert trace.get_current_span().get_span_context() == before
    assert secret not in caplog.text
