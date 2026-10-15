"""Trajectory runner policy, persistence and grading tests without model endpoints."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_memory_trajectory.py"
spec = importlib.util.spec_from_file_location("trajectory", SCRIPT)
trajectory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trajectory)


def test_remote_store_and_graph_rejected_before_output(tmp_path):
    for config in ({"vector_store": {"provider": "redis"}}, {"graph_store": {"provider": "neo4j"}}):
        with pytest.raises(ValueError):
            trajectory.run([], config, tmp_path / "run")
        assert not (tmp_path / "run").exists()


def test_phrase_grading_does_not_match_substrings():
    checks = trajectory.grade(
        {"contains": ["tea"], "excludes": ["coffee"]}, {"results": [{"memory": "Steak with teapot"}]}
    )
    assert checks == {"contains:tea": False, "excludes:coffee": True}


def test_stateful_correction_scope_and_reload(tmp_path):
    created = []
    state = {}

    class Provider:
        def generate_response(self, text):
            return text

    class Memory:
        def __init__(self, config):
            self.config = config
            self.llm = Provider()
            self.closed = False
            created.append(self)

        def add(self, text, user_id):
            state[user_id] = self.llm.generate_response(text)
            return {"results": []}

        def search(self, query, filters, top_k):
            assert top_k == 20
            return self.get_all(filters=filters, top_k=top_k)

        def get_all(self, filters, top_k):
            text = state.get(filters["user_id"])
            return {"results": [{"memory": text}] if text else []}

        def close(self):
            self.closed = True

    events = [
        {"id": "old", "action": "add", "user": "alice", "text": "coffee"},
        {"id": "correction", "action": "add", "user": "alice", "text": "tea"},
        {"id": "restart", "action": "reload"},
        {
            "id": "recall",
            "action": "search",
            "user": "alice",
            "query": "drink",
            "expect": {"contains": ["tea"], "excludes": ["coffee"]},
        },
        {"id": "isolation", "action": "get_all", "user": "bob", "expect": {"empty": True}},
    ]
    summary = trajectory.run(events, {}, tmp_path / "run", memory_factory=Memory)
    assert summary["passed"] and summary["checks_total"] == 3
    assert len(created) == 2 and all(item.closed for item in created)
    assert created[0].config["vector_store"]["config"]["path"] == str(tmp_path / "run" / "qdrant")
    spans = [json.loads(line) for line in (tmp_path / "run" / "traces.jsonl").read_text().splitlines()]
    assert len(spans) == 2 and {span["stage_id"] for span in spans} == {"old", "correction"}
    assert all(span["root_trace_id"] == summary["root_trace_id"] for span in spans)


def test_provider_exception_not_confused_with_failed_grade(tmp_path):
    class Provider:
        def generate_response(self, text):
            raise RuntimeError("secret token must not be recorded")

    class Memory:
        def __init__(self, config):
            self.llm = Provider()

        def add(self, text, user_id):
            return self.llm.generate_response(text)

        def close(self):
            pass

    summary = trajectory.run(
        [{"id": "error", "action": "add", "user": "alice", "text": "hello"}],
        {},
        tmp_path / "run",
        memory_factory=Memory,
    )
    assert not summary["passed"] and summary["operation_errors"] == 1
    assert summary["checks_total"] == 0
    assert "secret token" not in (tmp_path / "run" / "traces.jsonl").read_text()


def test_cleanup_error_preserves_summary_and_operation_results(tmp_path):
    class Memory:
        def __init__(self, config):
            pass

        def get_all(self, filters, top_k):
            return {"results": []}

        def close(self):
            raise RuntimeError("secret cleanup detail")

    result = trajectory.run(
        [{"id": "empty", "action": "get_all", "user": "alice", "expect": {"empty": True}}],
        {},
        tmp_path / "run",
        memory_factory=Memory,
    )
    assert not result["passed"] and result["cleanup_error_type"] == "RuntimeError"
    assert (tmp_path / "run" / "summary.json").exists()
    assert result["checks_passed"] == 1
    assert "secret cleanup" not in (tmp_path / "run" / "summary.json").read_text()


def test_no_assertions_is_explicit_operational_smoke(tmp_path):
    class Memory:
        def __init__(self, config):
            pass

        def get_all(self, filters, top_k):
            return {"results": []}

        def close(self):
            pass

    result = trajectory.run(
        [{"id": "list", "action": "get_all", "user": "alice"}], {}, tmp_path / "run", memory_factory=Memory
    )
    assert result["passed"] and result["quality_passed"] is None
    assert result["assessment"] == "operation_smoke_only"

def test_malformed_memory_envelope_is_ungradable_and_stops_trajectory(tmp_path):
    class Memory:
        def __init__(self, config):
            pass

        def get_all(self, filters, top_k):
            return {"error": "broken adapter"}

        def close(self):
            pass

    events = [
        {"id": "corrupt", "action": "get_all", "user": "alice", "expect": {"empty": True}},
        {"id": "must-not-run", "action": "get_all", "user": "alice", "expect": {"empty": True}},
    ]
    result = trajectory.run(events, {}, tmp_path / "run", memory_factory=Memory)
    assert result["passed"] is False and result["quality_passed"] is None
    assert result["ungradable_outputs"] == 1
    assert result["checks_passed"] == result["checks_total"] == 0
    outcomes = [json.loads(line) for line in (tmp_path / "run" / "outcomes.jsonl").read_text().splitlines()]
    assert [row["stage_id"] for row in outcomes] == ["corrupt"]
    assert outcomes[0]["status"] == "ungradable_output"
