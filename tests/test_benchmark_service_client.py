"""Transport boundary checks; no service/model requests."""

import io
import sys
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from benchmark_service_client import (  # noqa: E402
    LocalServiceClient,
    NoRedirect,
    ServiceFailure,
)

PREFIX = "evaluation-unique-001-"


def client():
    result = LocalServiceClient("http://127.0.0.1:8765", "synthetic-key", PREFIX)
    result._opener = Mock()
    return result


def test_auth_scope_and_request_are_preserved():
    instance = client()
    instance._opener.open.return_value = io.BytesIO(b'{"results":[]}')
    assert instance.call("search", {"query": "tea", "user_id": PREFIX + "a", "limit": 10}) == {"results": []}
    req = instance._opener.open.call_args.args[0]
    assert req.get_header("X-api-key") == "synthetic-key"
    assert req.full_url == "http://127.0.0.1:8765/search"
    assert instance._opener.open.call_count == 1
    assert instance.last_evidence["status"] == "success"
    assert instance.last_evidence["elapsed_ms"] >= 0


@pytest.mark.parametrize("body", [b'{}', b'not-json', b'{"results":[{"id":"a"}]}'])
def test_malformed_results_fail_once(body):
    instance = client()
    instance._opener.open.return_value = io.BytesIO(body)
    with pytest.raises(ServiceFailure, match="Malformed"):
        instance.call("list", {"user_id": PREFIX + "a"})
    assert instance._opener.open.call_count == 1


def test_http_failure_is_redacted_and_not_retried():
    instance = client()
    instance._opener.open.side_effect = urllib.error.HTTPError("secret-url", 401, "secret-body", {}, None)
    with pytest.raises(ServiceFailure, match="HTTP status 401") as exc:
        instance.call("list", {"user_id": PREFIX + "a"})
    assert "secret" not in str(exc.value) and instance._opener.open.call_count == 1
    assert instance.last_evidence["http_status"] == 401
    assert instance.last_evidence["status"] == "error"


def test_unscoped_requests_and_remote_origins_rejected():
    instance = client()
    with pytest.raises(ValueError):
        instance.call("list", {"user_id": "production-user"})
    instance._opener.open.assert_not_called()
    with pytest.raises(ValueError):
        LocalServiceClient("http://example.com", "key", PREFIX)


def test_redirect_handler_refuses_forwarding():
    assert NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://example.com") is None


def test_real_loopback_http_encoding_auth_and_redirect_refusal():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured.append((self.path, self.headers.get("X-API-Key")))
            if "redirect" in self.path:
                self.send_response(302)
                self.send_header("Location", "/must-not-receive-key")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Request-ID", "abcd1234")
                self.end_headers()
                self.wfile.write(b'{"results":[]}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        instance = LocalServiceClient(f"http://127.0.0.1:{server.server_port}", "synthetic-key", PREFIX, timeout=2)
        assert instance.call("list", {"user_id": PREFIX + "space value", "limit": 10}) == {"results": []}
        assert captured == [(f"/memories?user_id={PREFIX}space+value&top_k=10", "synthetic-key")]
        assert instance.last_evidence["request_id"] == "abcd1234"
        with pytest.raises(ServiceFailure, match="HTTP status 302"):
            instance.call("list", {"user_id": PREFIX + "redirect"})
        assert len(captured) == 2
        assert all("must-not-receive-key" not in path for path, _ in captured)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
