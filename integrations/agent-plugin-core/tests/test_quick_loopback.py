from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

CORE_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER = CORE_ROOT / "python" / "mcp_server.py"


class LoopbackMem0:
    def __init__(self) -> None:
        self.status = 200
        self.body: dict[str, Any] = {"results": []}

        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                payload = json.dumps(api.body).encode("utf-8")
                self.send_response(api.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def loopback_mem0() -> Any:
    server = LoopbackMem0()
    try:
        yield server
    finally:
        server.close()


def _isolated_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = subprocess.run(
        ["git", "init", str(repo)], capture_output=True, text=True, timeout=5, check=False
    )
    assert result.returncode == 0, (
        f"git init failed: stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    return repo


def _run_stdio_mcp(
    tmp_path: Path,
    loopback_mem0: LoopbackMem0,
    *,
    calls: list[dict[str, Any]],
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    repo = _isolated_repo(tmp_path)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        *calls,
        {"jsonrpc": "2.0", "id": 99, "method": "ping"},
    ]
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    data_dir = tmp_path / "plugin-data"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(isolated_home),
        "TMPDIR": str(tmp_path),
        "NO_COLOR": "1",
        "MEM0_API_URL": loopback_mem0.url,
        "MEM0_API_KEY": "loopback-test-key",
        "MEM0_USER_ID": "loopback-test-user",
        "MEM0_CODE_DATA_DIR": str(data_dir),
        "MEM0_PLUGIN_DATA_DIR": str(data_dir),
        "PLUGIN_DATA": str(data_dir),
        "CLAUDE_PLUGIN_DATA": str(data_dir),
        "MEM0_TELEMETRY": "false",
        "CLAUDE_PROJECT_DIR": str(repo),
    }
    completed = subprocess.run(
        [sys.executable, str(MCP_SERVER)],
        input="".join(json.dumps(message) + "\n" for message in requests),
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
        check=False,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    return completed, responses


def _search_call(request_id: int, query: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "search_memories", "arguments": {"query": query}},
    }


def _assert_protocol_responses(
    completed: subprocess.CompletedProcess[str], responses: list[dict[str, Any]]
) -> None:
    assert completed.returncode == 0, (
        f"stdio server exit={completed.returncode}, stdout={completed.stdout!r}, "
        f"stderr={completed.stderr!r}"
    )
    response_ids = [message["id"] for message in responses]
    assert response_ids == [1, 2, 3, 99], (
        f"unexpected MCP response IDs={response_ids!r}, "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert responses[-1] == {"jsonrpc": "2.0", "id": 99, "result": {}}


def test_p05_stdio_mcp_marks_loopback_http_503_as_tool_error_and_keeps_protocol(
    tmp_path: Path, loopback_mem0: LoopbackMem0
) -> None:
    loopback_mem0.status = 503
    loopback_mem0.body = {"detail": "synthetic upstream unavailable"}

    completed, responses = _run_stdio_mcp(
        tmp_path,
        loopback_mem0,
        calls=[_search_call(3, "query that receives controlled HTTP 503")],
    )

    _assert_protocol_responses(completed, responses)
    tools = responses[1]["result"]["tools"]
    advertised = next(tool for tool in tools if tool["name"] == "search_memories")
    assert advertised["inputSchema"]["properties"]["query"]["type"] == "string"
    failure = responses[2]["result"]
    assert failure["isError"] is True, (
        f"P05 expected isError=true; actual result={failure!r}, "
        f"exit={completed.returncode}, stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


def test_stdio_mcp_success_returns_known_loopback_memory(
    tmp_path: Path, loopback_mem0: LoopbackMem0
) -> None:
    loopback_mem0.body = {
        "results": [{"id": "known-memory-1", "memory": "Fixture memory: use pathlib for paths."}]
    }

    completed, responses = _run_stdio_mcp(
        tmp_path,
        loopback_mem0,
        calls=[_search_call(3, "how should paths be handled?")],
    )

    _assert_protocol_responses(completed, responses)
    result = responses[2]["result"]
    assert result["isError"] is False
    content = "\n".join(item["text"] for item in result["content"])
    assert "Fixture memory: use pathlib for paths." in content


def test_stdio_mcp_empty_success_is_not_reported_as_search_failure(
    tmp_path: Path, loopback_mem0: LoopbackMem0
) -> None:
    loopback_mem0.body = {"results": []}

    completed, responses = _run_stdio_mcp(
        tmp_path,
        loopback_mem0,
        calls=[_search_call(3, "query with no matching memories")],
    )

    _assert_protocol_responses(completed, responses)
    result = responses[2]["result"]
    assert result["isError"] is False
