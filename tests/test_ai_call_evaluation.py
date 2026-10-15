"""Regression coverage for evaluation evidence, grading, bounds and failure accounting."""

import importlib.util
import json
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_ai_calls", Path(__file__).parents[1] / "scripts/evaluate_ai_calls.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def case(output='{"memory": []}', **expect):
    return {
        "id": "test",
        "task": "llm",
        "input": {"messages": []},
        "expect": expect or {"json_required": True, "required_keys": ["memory"]},
        "mock_output": output,
    }


def test_malformed_output_and_simulated_failure_remain_in_denominator(tmp_path):
    cases = [case("not json"), dict(case(), id="transport", mock_error="secret-key")]
    summary = evaluation.evaluate(cases, {}, tmp_path / "run", repeat=2, concurrency=2, offline=True)
    assert summary["trials"] == 4
    assert summary["passed"] == 0
    assert summary["error_count"] == 2
    assert not summary["gate_passed"]
    rows = [json.loads(line) for line in (tmp_path / "run/trials.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert all(r["evidence"] == "offline_simulation" and r["usage"] is None for r in rows)
    assert "secret-key" not in (tmp_path / "run/trials.jsonl").read_text()


def test_config_values_not_written_and_existing_artifacts_not_overwritten(tmp_path):
    target = tmp_path / "run"
    evaluation.evaluate(
        [case()], {"llm": {"config": {"api_key": "TOP_SECRET", "base_url": "private-host"}}}, target, offline=True
    )
    assert "TOP_SECRET" not in (target / "manifest.json").read_text()
    assert "private-host" not in (target / "manifest.json").read_text()
    with pytest.raises(FileExistsError):
        evaluation.evaluate([case()], {}, target, offline=True)


def test_live_adapter_failure_and_latency_budget_do_not_claim_cancellation(tmp_path):
    class Adapter:
        def generate_response(self, **kwargs):
            time.sleep(0.02)
            return '{"memory": []}'

    summary = evaluation.evaluate(
        [case()],
        {"llm": {"provider": "fake"}},
        tmp_path / "live",
        latency_budget_ms=1,
        adapter_factory=lambda *args: Adapter(),
    )
    assert summary["passed"] == 0
    row = json.loads((tmp_path / "live/trials.jsonl").read_text())
    assert row["status"] == "failed_expectation"
    assert row["latency_ms"] >= 10
    assert row["output"] == '{"memory": []}'


def test_initialization_failure_is_recorded_without_leaking_error(tmp_path):
    def broken(*args):
        raise ValueError("credential SECRET")

    summary = evaluation.evaluate([case()], {"llm": {"provider": "fake"}}, tmp_path / "live", adapter_factory=broken)
    assert summary["error_count"] == 1
    row = json.loads((tmp_path / "live/trials.jsonl").read_text())
    assert row["status"] == "initialization_error" and row["latency_ms"] is None
    assert "SECRET" not in json.dumps(row)


def test_embedding_batch_shape_nonfinite_and_boolean_rejected():
    fixture = {"task": "embedding", "input": {"texts": ["a", "b"]}, "expect": {"dimensions": 2}}
    assert not all(evaluation.grade(fixture, [[1, 2]]).values())
    assert not all(evaluation.grade(fixture, [[1, 2], [1, float("nan")]]).values())
    assert not all(evaluation.grade(fixture, [[1, 2], [True, 2]]).values())
    assert all(evaluation.grade(fixture, [[1, 2], [3, 4]]).values())


def test_semantic_checks_reject_valid_json_wrong_action():
    fixture = case(memory_items=[{"event": "UPDATE", "id": "7", "contains": ["tea"]}], memory_count=1)
    assert not all(evaluation.grade(fixture, '{"memory":[{"event":"ADD","id":"7","text":"tea"}]}').values())
    assert all(evaluation.grade(fixture, '{"memory":[{"event":"UPDATE","id":"7","text":"Tea"}]}').values())


def test_wilson_interval_and_quantile():
    lower, upper = evaluation.wilson(10, 10)
    assert 0.72 < lower < 0.73 and upper == pytest.approx(1)
    assert evaluation.quantile([10, 20, 30], 0.5) == 20
    assert evaluation.quantile([], 0.5) is None


def test_bounds_and_unknown_expectation_fail_before_artifacts(tmp_path):
    with pytest.raises(ValueError):
        evaluation.evaluate([case()], {}, tmp_path / "run", repeat=101, offline=True)
    with pytest.raises(ValueError):
        evaluation.evaluate([case(typo=True)], {}, tmp_path / "run", offline=True)
    assert not (tmp_path / "run").exists()


def test_cli_gate_exit_and_offline_evidence(tmp_path):
    fixtures = tmp_path / "cases.jsonl"
    fixtures.write_text(json.dumps(case("broken")) + "\n")
    assert evaluation.main(["--cases", str(fixtures), "--output-dir", str(tmp_path / "run"), "--offline"]) == 1


@pytest.mark.parametrize(
    "expect",
    [
        {"json_required": False},
        {"contains": False},
        {"contains": []},
        {"dimensions": -1},
        {"dimensions": True},
        {"memory_count": -1},
    ],
)
def test_vacuous_or_malformed_expectations_rejected_before_artifacts(tmp_path, expect):
    with pytest.raises(ValueError):
        evaluation.evaluate([case(**expect)], {}, tmp_path / "run", offline=True)
    assert not (tmp_path / "run").exists()


def test_malformed_task_input_rejected_before_artifacts(tmp_path):
    fixture = case()
    fixture["input"] = {"text": "missing messages"}
    with pytest.raises(ValueError):
        evaluation.evaluate([fixture], {}, tmp_path / "run", offline=True)
    assert not (tmp_path / "run").exists()


def test_provider_identity_and_disaggregated_summary(tmp_path):
    fixtures = [
        case(),
        {
            "id": "embed",
            "task": "embedding",
            "input": {"text": "tea"},
            "expect": {"dimensions": 2},
            "mock_output": [1, 2],
        },
    ]
    config = {
        "llm": {
            "provider": "ollama",
            "config": {
                "model": "qwen3:8b",
                "temperature": 0.1,
                "max_tokens": 128,
                "api_key": "SECRET",
                "ollama_base_url": "PRIVATE_ENDPOINT",
            },
        }
    }
    summary = evaluation.evaluate(fixtures, config, tmp_path / "run", offline=True)
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    assert manifest["providers"]["llm"]["requested_model"] == "qwen3:8b"
    assert manifest["providers"]["llm"]["temperature"] == 0.1
    assert manifest["providers"]["llm"]["actual_model"] is None
    assert "SECRET" not in json.dumps(manifest) and "PRIVATE_ENDPOINT" not in json.dumps(manifest)
    assert summary["by_task"]["embedding"]["trials"] == 1
    assert summary["by_case"]["test"]["passed"] == 1
