from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


class _MemoryAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.request_log.append({"method": "GET", "path": self.path})
        if self.path == "/v1/ping/":
            self._respond(200, {"message": "pong"})
        elif self.path.startswith("/v1/memories/"):
            self._respond(
                200,
                {
                    "id": "target-memory-id",
                    "memory": "protected target memory",
                    "created_at": "2026-10-20",
                },
            )
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.request_log.append(
            {"method": "POST", "path": path, "body": payload}
        )
        if path == "/v3/memories/":
            filters = payload.get("filters") or {}
            if not any(filters.get(key) for key in ("user_id", "agent_id", "run_id")):
                self._respond(
                    400,
                    {
                        "detail": (
                            "filters must positively contain at least one of "
                            "user_id, agent_id, run_id"
                        )
                    },
                )
                return
            results = [
                memory for memory in self.server.memories
                if all(memory.get(key) == value for key, value in filters.items())
            ]
            query = parse_qs(urlsplit(self.path).query)
            page = int(query.get("page", ["1"])[0])
            page_size = int(query.get("page_size", ["100"])[0])
            start = (page - 1) * page_size
            self._respond(200, {"results": results[start:start + page_size]})
            return
        if path != "/v3/memories/add/":
            self.send_error(404)
            return
        messages = payload.get("messages", [])
        memory = messages[0].get("content", "") if messages else ""
        if memory.startswith("controlled credential rejection"):
            self._respond(400, {"detail": "rejected credential: loopback-only-test-key"})
        elif memory.startswith("controlled failure"):
            self._respond(503, {"detail": "controlled loopback failure"})
        else:
            self._respond(
                200,
                {
                    "results": [
                        {
                            "id": "fixture-memory-id",
                            "memory": memory,
                            "event": "ADD",
                        }
                    ],
                    "event_id": "fixture-event-id",
                },
            )

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        self.server.request_log.append({"method": "DELETE", "path": path})
        if path == "/v1/memories/":
            self.server.memories.clear()
        elif path.startswith("/v1/memories/"):
            memory_id = path.rstrip("/").split("/")[-1]
            self.server.memories[:] = [
                memory for memory in self.server.memories if memory["id"] != memory_id
            ]
        self._respond(200, {"deleted": True})

    def _respond(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _LoopbackAPI:
    def __init__(
        self, base_url: str, requests: list[dict], memories: list[dict]
    ) -> None:
        self.base_url = base_url
        self.requests = requests
        self.memories = memories


@pytest.fixture
def loopback_api():
    requests: list[dict] = []
    memories = [
        {
            "id": "target-memory-id",
            "memory": "protected target memory",
            "user_id": "alice",
            "created_at": "2026-10-20",
        },
        {
            "id": "sentinel-memory-id",
            "memory": "protected sentinel memory",
            "user_id": "bob",
            "created_at": "2026-10-19",
        },
    ]
    handler = type("FixtureMemoryAPIHandler", (_MemoryAPIHandler,), {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.request_log = requests
    server.memories = memories
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _LoopbackAPI(
            f"http://127.0.0.1:{server.server_port}", requests, memories
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_cli(
    tmp_path: Path,
    loopback_api: _LoopbackAPI,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "MEM0_",
                "LANGFUSE_",
                "OTEL_",
                "PLUGIN_OPTION_",
                "CLAUDE_PLUGIN_OPTION_",
            )
        )
        and not key.endswith(("_API_KEY", "_TOKEN", "_SECRET"))
        and key.lower()
        not in {
            "force_color",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
            "node_options",
            "claude_plugin_root",
            "claude_plugin_data",
        }
    }
    src = Path(__file__).parents[1] / "src"
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PYTHONPATH": str(src),
            "MEM0_API_KEY": "loopback-only-test-key",
            "MEM0_BASE_URL": loopback_api.base_url,
            "MEM0_USER_ID": "loopback-test-user",
            "MEM0_TELEMETRY": "false",
            "MEM0_TELEMETRY_DISABLED": "1",
            "DO_NOT_TRACK": "1",
            "NO_COLOR": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "mem0_cli", *args],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _run_import(
    tmp_path: Path,
    loopback_api: _LoopbackAPI,
    records: list[dict[str, str]],
    global_args: list[str],
    command_options: list[str],
) -> subprocess.CompletedProcess[str]:
    import_file = tmp_path / "memories.json"
    import_file.write_text(json.dumps(records), encoding="utf-8")
    return _run_cli(
        tmp_path,
        loopback_api,
        [
            *global_args,
            "import",
            *command_options,
            str(import_file),
            "--user-id",
            "loopback-test-user",
        ],
    )


@pytest.mark.parametrize(
    ("global_args", "command_options"),
    [(["--json"], []), ([], ["-o", "json"])],
    ids=["global-json", "output-json"],
)
def test_import_all_loopback_failures_return_error_json_and_nonzero(
    tmp_path: Path,
    loopback_api: _LoopbackAPI,
    global_args: list[str],
    command_options: list[str],
) -> None:
    result = _run_import(
        tmp_path,
        loopback_api,
        [{"memory": "controlled failure: all rows unavailable"}],
        global_args,
        command_options,
    )

    assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "import"
    assert envelope["status"] == "error"
    assert envelope["data"] == {"added": 0, "failed": 1}
    assert envelope.get("error"), f"failed import needs an error message: {envelope!r}"
    assert "503" in envelope["error"]


def test_import_success_preserves_known_count_and_json_envelope(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_import(
        tmp_path,
        loopback_api,
        [{"memory": "known successful loopback memory"}],
        [],
        ["-o", "json"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["command"] == "import"
    assert envelope["data"] == {"added": 1, "failed": 0}


def test_import_partial_loopback_failure_retains_known_counts_and_json_envelope(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_import(
        tmp_path,
        loopback_api,
        [
            {"memory": "known successful partial import memory"},
            {"memory": "controlled failure: one row unavailable"},
        ],
        ["--json"],
        [],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["command"] == "import"
    assert envelope["data"] == {"added": 1, "failed": 1}


@pytest.mark.parametrize(
    ("global_args", "command_options"),
    [(["--json"], []), ([], ["-o", "json"]), ([], [])],
    ids=["global-json", "output-json", "text"],
)
def test_import_failure_redacts_upstream_credentials(
    tmp_path: Path,
    loopback_api: _LoopbackAPI,
    global_args: list[str],
    command_options: list[str],
) -> None:
    result = _run_import(
        tmp_path,
        loopback_api,
        [{"memory": "controlled credential rejection"}],
        global_args,
        command_options,
    )

    assert result.returncode != 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "loopback-only-test-key" not in result.stdout + result.stderr
    if global_args or command_options:
        envelope = json.loads(result.stdout)
        assert envelope["status"] == "error"
        assert envelope["data"] == {"added": 0, "failed": 1}


def test_delete_all_json_dry_run_returns_scoped_preview_without_deleting(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_cli(
        tmp_path,
        loopback_api,
        [
            "--json",
            "delete",
            "--all",
            "--user-id",
            "alice",
            "--dry-run",
            "--force",
        ],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not any(request["method"] == "DELETE" for request in loopback_api.requests)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["command"] == "delete-all"
    assert envelope["scope"]["user_id"] == "alice"
    assert envelope["count"] == 1
    assert envelope["data"] == {"dry_run": True}

    listing = next(
        request
        for request in loopback_api.requests
        if request["method"] == "POST" and request["path"] == "/v3/memories/"
    )
    assert listing["body"]["filters"] == {"user_id": "alice"}


@pytest.mark.parametrize("row_count", [100, 137])
def test_scoped_delete_preview_counts_all_pages_without_deleting(
    tmp_path: Path, loopback_api: _LoopbackAPI, row_count: int
) -> None:
    sentinel = loopback_api.memories[1]
    loopback_api.memories[:] = [
        {"id": f"paged-{i}", "memory": f"Protected memory {i}", "user_id": "alice"}
        for i in range(row_count)
    ] + [sentinel]
    protected_ids = [memory["id"] for memory in loopback_api.memories]

    result = _run_cli(
        tmp_path, loopback_api,
        ["--json", "delete", "--all", "--user-id", "alice", "--dry-run", "--force"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    envelope = json.loads(result.stdout)
    assert envelope["count"] == row_count
    assert envelope["scope"]["user_id"] == "alice"
    assert envelope["data"]["dry_run"] is True
    assert not any(request["method"] == "DELETE" for request in loopback_api.requests)
    assert [memory["id"] for memory in loopback_api.memories] == protected_ids


def test_single_delete_json_dry_run_returns_preview_without_deleting(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_cli(
        tmp_path,
        loopback_api,
        ["--json", "delete", "target-memory-id", "--dry-run", "--force"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not any(request["method"] == "DELETE" for request in loopback_api.requests)
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "delete"
    assert envelope["status"] == "success"
    assert envelope["data"]["dry_run"] is True
    assert envelope["data"]["id"] == "target-memory-id"
    assert envelope["data"]["memory"] == "protected target memory"



def test_entity_delete_json_dry_run_returns_preview_without_deleting(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_cli(
        tmp_path,
        loopback_api,
        [
            "--json",
            "delete",
            "--entity",
            "--user-id",
            "alice",
            "--dry-run",
            "--force",
        ],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not any(request["method"] == "DELETE" for request in loopback_api.requests)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["command"] == "entity delete"
    assert envelope["scope"]["user_id"] == "alice"
    assert envelope["data"]["dry_run"] is True



def test_project_delete_json_dry_run_previews_scope_without_reading_or_deleting(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_cli(
        tmp_path,
        loopback_api,
        ["--json", "delete", "--all", "--project", "--dry-run", "--force"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not any(
        request["method"] == "DELETE" or request["path"] == "/v3/memories/"
        for request in loopback_api.requests
    )
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["command"] == "delete-all"
    assert envelope["data"] == {"dry_run": True}
    assert envelope["scope"]["project"] == "true"
    assert "count" not in envelope
    assert [memory["id"] for memory in loopback_api.memories] == [
        "target-memory-id",
        "sentinel-memory-id",
    ]



def test_project_delete_text_dry_run_does_not_delete(
    tmp_path: Path, loopback_api: _LoopbackAPI
) -> None:
    result = _run_cli(
        tmp_path,
        loopback_api,
        ["delete", "--all", "--project", "--dry-run", "--force"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "would delete all memories across the entire project" in result.stdout.lower()
    assert "dry run" in result.stdout.lower()
    assert not any(
        request["method"] == "DELETE" or request["path"] == "/v3/memories/"
        for request in loopback_api.requests
    )
    assert [memory["id"] for memory in loopback_api.memories] == [
        "target-memory-id",
        "sentinel-memory-id",
    ]
