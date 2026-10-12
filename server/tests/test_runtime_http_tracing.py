"""Execute actual middleware AST in a minimal app without database/model startup.

This verifies middleware integration, not import-time server setup or Docker.
"""

import ast
import asyncio
import importlib.util
import time
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SERVER = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("http_runtime_tracing", SERVER / "runtime_tracing.py")
tracing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracing)


def test_actual_middleware_links_sync_worker_provider_and_redacts_url():
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.http")
    request_id = ContextVar("test_http_request", default="-")
    app = FastAPI()
    tree = ast.parse((SERVER / "main.py").read_text())
    middleware = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "log_requests")
    namespace = {"app": app, "Request": object, "new_request_id": lambda: "abcdef12",
                 "request_id_var": request_id, "time": time, "asyncio": asyncio,
                 "request_span": tracing.request_span, "http_status": tracing.http_status,
                 "_runtime_tracer": tracer, "_should_log_request": lambda _request: False}
    exec(compile(ast.Module(body=[middleware], type_ignores=[]), str(SERVER / "main.py"), "exec"), namespace)

    from types import SimpleNamespace
    memory = SimpleNamespace(llm=SimpleNamespace(generate_response=lambda: "fixture output"))
    tracing.attach_memory(memory, tracer, request_id.get)

    @app.get("/fixture/{private_id}")
    def endpoint(private_id: str):
        return {"output": memory.llm.generate_response()}

    try:
        with TestClient(app) as client:
            response = client.get("/fixture/private-user-value?private-query=secret")
        assert response.status_code == 200 and response.headers["X-Request-ID"] == "abcdef12"
        assert response.json() == {"output": "fixture output"}
        spans = exporter.get_finished_spans()
        child = next(span for span in spans if span.name == "mem0.llm.generate_response")
        root = next(span for span in spans if span.name == "mem0.http")
        assert child.parent.span_id == root.context.span_id
        assert child.attributes["mem0.request_id"] == "abcdef12"
        assert root.attributes["http.response.status_code"] == 200
        assert "private" not in repr([dict(span.attributes) for span in spans])
        assert request_id.get() == "-"
    finally:
        provider.shutdown()


def test_shutdown_failure_is_nonfatal_and_not_readback_acceptance():
    from types import SimpleNamespace
    calls = []
    def flush(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("private exporter failure")
    provider = SimpleNamespace(force_flush=flush, shutdown=lambda: calls.append("shutdown"))
    assert tracing.shutdown_exporter(provider) is False
    assert calls == [{"timeout_millis": 5000}, "shutdown"]
    assert tracing.shutdown_exporter(None) is None
