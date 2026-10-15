from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOST_ROOT = Path(__file__).resolve().parents[1]
CORE = HOST_ROOT / "core"
ADAPTER = HOST_ROOT / "adapters" / "omp" / "index.mjs"
sys.path.insert(0, str(CORE))

import memory_core  # noqa: E402

NODE_SCENARIO = r"""
import { pathToFileURL } from 'node:url';
const { default: install } = await import(pathToFileURL(process.env.MEM0_OMP_ADAPTER).href);
const handlers = new Map();
const pi = {
  on: (event, handler) => handlers.set(event, handler),
  logger: { warn() {}, error() {} },
};
install(pi);
const ctx = {
  cwd: process.env.MEM0_TEST_CWD,
  sessionManager: { getSessionId: () => 'omp-test-session' },
};
async function emit(name, event = {}) {
  const handler = handlers.get(name);
  if (handler) await handler(event, ctx);
}
await emit('session_start');
await emit('before_agent_start', { prompt: 'The first distinctive OMP prompt for capture.' });
await emit('agent_end', { messages: [{ role: 'assistant', content: [{ type: 'text', text: 'First visible reply.' }, { type: 'thinking', thinking: 'private reasoning' }] }] });
await emit('before_agent_start', { prompt: 'The second distinctive OMP prompt for capture.' });
await emit('agent_end', { messages: [
  { role: 'assistant', content: [{ type: 'text', text: 'First visible reply.' }] },
  { role: 'assistant', content: [{ type: 'text', text: 'Second visible reply.' }, { type: 'toolCall', name: 'write' }] },
] });
await emit('agent_end', { messages: [
  { role: 'assistant', content: [{ type: 'text', text: 'First visible reply.' }] },
  { role: 'assistant', content: [{ type: 'text', text: 'Second visible reply.' }] },
] });
await emit('before_agent_start', { prompt: 'A third prompt whose reply is legitimately unchanged.' });
await emit('agent_end', { messages: [
  { role: 'assistant', content: [{ type: 'text', text: 'Second visible reply.' }] },
] });
await emit('tool_result', { toolName: 'write', input: { path: 'src/new.py', content: 'value = 1' }, content: [{ type: 'text', text: 'written' }], isError: false });
await emit('tool_result', { toolName: 'edit', input: { path: 'src/first.py', paths: ['src/first.py', 'src/second.py'], input: 'bounded patch' }, content: [{ type: 'text', text: 'edited two files' }], isError: false });
await emit('tool_result', { toolName: 'bash', input: { command: 'pytest -q' }, content: [{ type: 'text', text: '2 passed' }], isError: false });
await emit('tool_result', { toolName: 'bash', input: { command: 'pytest --invalid-flag' }, content: [{ type: 'text', text: 'fixture failure: invalid flag' }], isError: true });
await emit('session_shutdown');
"""

PAUSED_SCENARIO = r"""
import { pathToFileURL } from 'node:url';
const { default: install } = await import(pathToFileURL(process.env.MEM0_OMP_ADAPTER).href);
const handlers = new Map();
install({ on: (event, handler) => handlers.set(event, handler), logger: { warn() {}, error() {} } });
const ctx = { cwd: process.env.MEM0_TEST_CWD, sessionManager: { getSessionId: () => 'omp-paused-session' } };
await handlers.get('session_start')({}, ctx);
await handlers.get('before_agent_start')({ prompt: 'This prompt must not be captured while paused.' }, ctx);
await handlers.get('agent_end')({ messages: [{ role: 'assistant', content: 'Must not be saved.' }] }, ctx);
await handlers.get('session_shutdown')({}, ctx);
"""


def run_node(scenario: str, tmp_path: Path) -> Path:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for OMP adapter behavior tests")
    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env.update(
        {
            "MEM0_OMP_ADAPTER": str(ADAPTER),
            "MEM0_TEST_CWD": str(HOST_ROOT),
            "MEM0_CODE_DATA_DIR": str(data_dir),
            "MEM0_PLUGIN_PYTHON": sys.executable,
            "MEM0_CODE_MIN_QUERY_CHARS": "10000",
            "MEM0_TELEMETRY": "false",
        }
    )
    env.pop("MEM0_CODE_AUTO_FLUSH", None)
    for name in (
        "MEM0_API_KEY",
        "PLUGIN_OPTION_API_KEY",
        "CLAUDE_PLUGIN_OPTION_API_KEY",
        "CLAUDE_PLUGIN_OPTION_MEM0_API_KEY",
    ):
        env.pop(name, None)
    subprocess.run(
        [node, "--input-type=module", "-e", scenario],
        check=True,
        cwd=HOST_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return data_dir


def read_events(data_dir: Path) -> list[dict]:
    with sqlite3.connect(data_dir / "evidence.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in connection.execute(
                "SELECT kind, payload_json FROM events ORDER BY id"
            )
        ]


def test_omp_captures_turns_once_and_hands_off_on_shutdown(tmp_path: Path) -> None:
    data_dir = run_node(NODE_SCENARIO, tmp_path)
    events = read_events(data_dir)

    prompts = [event["payload"]["text"] for event in events if event["kind"] == "user_prompt"]
    replies = [event["payload"]["text"] for event in events if event["kind"] == "assistant_stop"]
    assert prompts == [
        "The first distinctive OMP prompt for capture.",
        "The second distinctive OMP prompt for capture.",
        "A third prompt whose reply is legitimately unchanged.",
    ]
    assert replies == ["First visible reply.", "Second visible reply.", "Second visible reply."]
    tools = [event["payload"] for event in events if event["kind"] == "tool_result"]
    assert [tool["path"] for tool in tools if "path" in tool] == [
        "src/new.py", "src/first.py", "src/second.py"
    ]
    command = next(tool for tool in tools if tool.get("command") == "pytest -q")
    assert command["command_kind"] == "test"
    assert command["result_preview"] == "2 passed"
    failures = [event["payload"] for event in events if event["kind"] == "tool_failure"]
    assert len(failures) == 1
    assert failures[0]["failed"] is True
    assert failures[0]["error"] == "fixture failure: invalid flag"
    with sqlite3.connect(data_dir / "evidence.sqlite3") as connection:
        queued = connection.execute(
            "SELECT reason, event_start, event_end FROM flushes"
        ).fetchall()
    assert queued == [("session-end", 1, len(events))]


def test_omp_pause_prevents_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MEM0_CODE_DATA_DIR", str(data_dir))
    store = memory_core.EvidenceStore()
    try:
        store.set_setting("paused", "true")
    finally:
        store.close()

    run_node(PAUSED_SCENARIO, tmp_path)
    events = read_events(data_dir)
    assert events == []
