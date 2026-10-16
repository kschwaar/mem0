"""Whole-run supervision tests use real subprocesses, never model endpoints."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_bounded_trajectory.py"
spec = importlib.util.spec_from_file_location("bounded_trajectory", SCRIPT)
bounded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded)


@pytest.mark.parametrize("fault", ["changed_source", "over_budget", "changed_overlay"])
def test_source_launch_rejects_unreviewed_work_before_dispatch(tmp_path, monkeypatch, fault):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{
        "question_id": "q", "haystack_session_ids": ["s"], "haystack_dates": ["2023-01-01"],
        "haystack_sessions": [[{"role": "user", "content": "x" * (250001 if fault == "over_budget" else 1)}]],
    }]))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if fault == "changed_source":
        source.write_text(source.read_text() + " ")
    root = tmp_path / "benchmark"
    overlay = root / "benchmarks/longmemeval/run.py"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("raise AssertionError('must not dispatch')")
    overlay_digest = hashlib.sha256(overlay.read_bytes()).hexdigest()
    if fault == "changed_overlay":
        overlay.write_text("raise AssertionError('changed')")
    output = tmp_path / "run"
    with pytest.raises(ValueError):
        bounded.supervise_source_ingestion(root, output, source, digest, "q", overlay_digest,
                                           "http://127.0.0.1:8000", env={})
    assert not output.exists()
    assert not Path(str(output) + ".manifest.json").exists()
    assert not Path(str(output) + ".control.json").exists()


def test_source_launch_records_fixed_serial_protocol_without_credentials(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{
        "question_id": "q", "haystack_session_ids": ["s"], "haystack_dates": ["2023-01-01"],
        "haystack_sessions": [[{"role": "user", "content": "tea"}]],
    }]))
    root = tmp_path / "benchmark"
    overlay = root / "benchmarks/longmemeval/run.py"
    overlay.parent.mkdir(parents=True)
    # Synthetic child observes the real launch protocol; no model/service connection.
    overlay.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "out = Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
        "out.mkdir()\n"
        "(out / 'observed.json').write_text(json.dumps({'args': sys.argv[1:], "
        "'backend': os.environ['MEM0_BACKEND']}))\n"
    )
    output = tmp_path / "run"
    secret = "test-only-secret-do-not-persist"
    result = bounded.supervise_source_ingestion(
        root, output, source, hashlib.sha256(source.read_bytes()).hexdigest(), "q",
        hashlib.sha256(overlay.read_bytes()).hexdigest(), "http://127.0.0.1:8000", wall_seconds=5,
        env={"MEM0_EVAL_API_KEY": secret, "MEM0_EVAL_SCOPE_PREFIX": "isolated-test-prefix-"},
    )
    observed = json.loads((output / "observed.json").read_text())
    assert observed == {"backend": "product-local", "args": [
        "--project-name", "bounded-product-local",
        "--dataset-path", str(source), "--question-id", "q", "--ingest-only",
        "--max-workers", "1", "--max-history-chunks", "100", "--max-history-characters", "250000",
        "--output-dir", str(output), "--mem0-host", "http://127.0.0.1:8000",
    ]}
    manifest = Path(str(output) + ".manifest.json").read_text()
    assert secret not in manifest and "isolated-test-prefix-" not in manifest
    assert json.loads(manifest)["plan"]["chunks"] == 1
    assert result["ingestion_work_reconciled"] is False


def test_timeout_is_incomplete_and_does_not_retry(tmp_path):
    report = tmp_path / "control.json"
    result = bounded.supervise(
        [sys.executable, "-c", "import time; time.sleep(60)"], report, wall_seconds=0.15
    )
    assert result["state"] == "interrupted_wall_budget"
    assert result["attempts"] == 1 and result["passed"] is False
    assert result["server_cancellation"] == "unproven"
    assert result["child_exit_code"] is not None
    assert json.loads(report.read_text()) == result


def test_finished_child_preserves_failure(tmp_path):
    result = bounded.supervise([sys.executable, "-c", "raise SystemExit(1)"], tmp_path / "control.json", 2)
    assert result["state"] == "child_finished"
    assert result["child_exit_code"] == 1 and result["passed"] is False


def test_missing_report_parent_is_created_before_launch(tmp_path):
    report = tmp_path / "permanent" / "runs" / "control.json"
    result = bounded.supervise([sys.executable, "-c", "pass"], report, 2)
    assert result["state"] == "child_finished" and result["child_exit_code"] == 0
    assert json.loads(report.read_text()) == result


def test_existing_report_prevents_child_launch(tmp_path):
    report = tmp_path / "control.json"
    report.write_text("original")
    marker = tmp_path / "must-not-exist"
    with pytest.raises(FileExistsError):
        bounded.supervise([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], report, 2)
    assert report.read_text() == "original" and not marker.exists()


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True])
def test_invalid_budgets_fail_before_launch(tmp_path, seconds):
    with pytest.raises(ValueError):
        bounded.supervise(["not-a-real-command"], tmp_path / "control.json", seconds)


def test_tiny_envelope_requires_valid_events_and_add_caps():
    events = [{"id": str(i), "action": "add", "user": "test", "text": "coffee"} for i in range(4)]
    with pytest.raises(ValueError):
        bounded.validate_envelope(events, max_events=9, max_adds=3)
    with pytest.raises(ValueError):
        bounded.validate_envelope([{"id": "bad", "action": "get_all", "user": ""}], 9, 3)


def test_interrupted_run_cannot_pass_even_if_child_summary_claims_success(tmp_path):
    (tmp_path / "outcomes.jsonl").write_text('{"stage_id": "a", "status": "success"}\n')
    (tmp_path / "summary.json").write_text(json.dumps({"passed": True, "events_planned": 1, "events_completed": 1}))
    result = bounded.reconcile(
        tmp_path, [{"id": "a"}], {"state": "interrupted_wall_budget", "child_exit_code": 0}
    )
    assert result["trajectory_checks_passed"] is False


def test_partial_ledger_retains_finished_rows_and_missing_ids(tmp_path):
    (tmp_path / "outcomes.jsonl").write_text('{"stage_id": "a", "status": "success"}\n{"stage_id":')
    result = bounded.reconcile(tmp_path, [{"id": "a"}, {"id": "b"}], {"state": "child_finished", "child_exit_code": 0})
    assert result["recorded_event_ids"] == ["a"]
    assert result["events_without_outcomes"] == ["b"]
    assert result["ledger_error"] == "partial_or_malformed_ledger"
    assert not result["trajectory_checks_passed"]


def test_complete_ledger_requires_matching_successful_summary(tmp_path):
    (tmp_path / "outcomes.jsonl").write_text('{"stage_id": "a", "status": "success"}\n')
    events = [{"id": "a"}]
    control = {"state": "child_finished", "child_exit_code": 0}
    assert not bounded.reconcile(tmp_path, events, control)["trajectory_checks_passed"]
    (tmp_path / "summary.json").write_text(json.dumps({"passed": True, "events_planned": 1, "events_completed": 1}))
    assert bounded.reconcile(tmp_path, events, control)["trajectory_checks_passed"]


def test_existing_reconciliation_stops_cli_before_inference(tmp_path):
    config = tmp_path / "config.json"
    dataset = tmp_path / "events.json"
    output = tmp_path / "run"
    config.write_text("{}")
    dataset.write_text('[{"id":"a","action":"get_all","user":"alice"}]')
    reconciliation = Path(str(output) + ".reconciliation.json")
    reconciliation.write_text("original")
    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--dataset", str(dataset), "--output", str(output)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 2 and "FileExistsError" in result.stderr
    assert not output.exists() and reconciliation.read_text() == "original"


def test_ingestion_watchdog_timeout_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    result = bounded.supervise_ingestion(
        [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "run",
        {"question_id": "q", "chunks": 1}, wall_seconds=0.15,
    )
    assert result["control"]["state"] == "interrupted_wall_budget"
    assert not result["ingestion_work_reconciled"]
    assert result["quality_acceptance"] == "unproven"
    assert Path(str(tmp_path / "run") + ".reconciliation.json").exists()
