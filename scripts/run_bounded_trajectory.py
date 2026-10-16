"""Supervise the isolated trajectory CLI with a whole-run budget, never retrying.

This watchdog bounds the evaluation process, not backend execution. A killed
client does not prove that an already dispatched inference was cancelled.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/evaluate_memory_trajectory.py"


def validate_envelope(events, max_events=9, max_adds=3):
    for value in (max_events, max_adds):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("Event/add caps must be positive integers")
    spec = importlib.util.spec_from_file_location("bounded_trajectory_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_events(events)
    if len(events) > max_events or sum(e["action"] == "add" for e in events) > max_adds:
        raise ValueError("Trajectory exceeds event/add budget")


def supervise(command, report_path, wall_seconds=300, env=None):
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(wall_seconds)
        or wall_seconds <= 0
    ):
        raise ValueError("Whole-run budget must be positive finite seconds")
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the report before launching; never overwrite another run's evidence.
    with report_path.open("x") as stream:
        started = time.monotonic()
        result = {
            "schema_version": 1,
            "state": "starting",
            "attempts": 1,
            "wall_budget_seconds": wall_seconds,
            "passed": False,
            "quality_verified": False,
            "server_cancellation": "unproven",
            "scope": "Process watchdog only; child results and resource readiness require separate review",
        }

        def persist():
            stream.seek(0)
            stream.truncate()
            json.dump(result, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        persist()
        child = None
        try:
            child = subprocess.Popen(command, env=env)
            result.update(state="running", child_pid=child.pid)
            persist()
            try:
                code = child.wait(timeout=wall_seconds)
                result.update(state="child_finished", child_exit_code=code, child_successful=code == 0)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    code = child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    code = child.wait(timeout=3)
                result.update(state="interrupted_wall_budget", child_exit_code=code, child_successful=False)
        except BaseException as exc:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            result.update(state="supervisor_error", error_type=type(exc).__name__, child_successful=False)
            persist()
            raise
        finally:
            result["elapsed_seconds"] = time.monotonic() - started
            persist()
    return result


def reconcile(output, events, control):
    """Inventory actual ledgers without fabricating outcomes for interrupted work."""
    directory = Path(output)
    rows = []
    ledger = directory / "outcomes.jsonl"
    ledger_error = None
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                ledger_error = "partial_or_malformed_ledger"
                break
    ids = [row.get("stage_id") for row in rows]
    planned = [event["id"] for event in events]
    summary = None
    summary_path = directory / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            ledger_error = "malformed_summary"
    verified = (
        control["state"] == "child_finished"
        and control.get("child_exit_code") == 0
        and ledger_error is None
        and ids == planned
        and all(row.get("status") == "success" for row in rows)
        and isinstance(summary, dict)
        and summary.get("passed") is True
        and summary.get("events_planned") == len(events)
        and summary.get("events_completed") == len(events)
    )
    return {
        "state": control["state"],
        "events_planned": len(events),
        "recorded_event_ids": ids,
        "events_without_outcomes": [stage for stage in planned if stage not in ids],
        "ledger_error": ledger_error,
        "child_summary": summary,
        "trajectory_checks_passed": verified,
        "quality_acceptance": "unproven; authored smoke and phrase proxies only",
        "server_cancellation": "unproven",
    }


def supervise_ingestion(command, output, plan, wall_seconds=900, env=None):
    """Whole-run watchdog plus fail-closed stage reconciliation; never retries.

    Caller must validate immutable source/workload, isolated storage and readiness.
    This function is not an authorization to launch a benchmark.
    """
    from benchmark_async_backend import reconcile_ingestion_stage

    output = Path(output)
    control_path = Path(str(output) + ".control.json")
    result_path = Path(str(output) + ".reconciliation.json")
    if any(path.exists() for path in (output, control_path, result_path)):
        raise FileExistsError("Fresh ingestion artifacts required; automatic resume is unsupported")
    # Validate the supplied plan before dispatch, independently of child outcomes.
    reconcile_ingestion_stage(output, plan, {})
    control = supervise(command, control_path, wall_seconds, env)
    result = reconcile_ingestion_stage(output, plan, control)
    result["control"] = control
    with result_path.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return result


def supervise_source_ingestion(benchmark_root, output, dataset_path, dataset_sha256,
                              question_id, overlay_sha256, host, wall_seconds=900, env=None,
                              predict_only=False, max_chunks=100, max_characters=250000):
    """Fixed single-history launch, source verified before any child dispatch.

    Independently review source/overlay hashes, service isolation and readiness.
    This checks provenance/work counts, not the running server's model identity.
    """
    from audit_benchmark_overlap import plan_history

    plan = plan_history(dataset_path, dataset_sha256, question_id, max_chunks, max_characters)
    root = Path(benchmark_root).resolve()
    overlay = root / "benchmarks/longmemeval/run.py"
    if hashlib.sha256(overlay.read_bytes()).hexdigest() != overlay_sha256:
        raise ValueError("Prepared benchmark overlay does not match reviewed source")
    output = Path(output).resolve()
    manifest_path = Path(str(output) + ".manifest.json")
    if any(path.exists() for path in (output, manifest_path, Path(str(output) + ".control.json"),
                                     Path(str(output) + ".reconciliation.json"))):
        raise FileExistsError("Fresh source-supervised artifacts required")
    effective_env = dict(os.environ if env is None else env)
    effective_env["MEM0_BACKEND"] = "product-local"
    effective_env["PYTHONPATH"] = os.pathsep.join((str(root), str(ROOT / "scripts")))
    # Validate origin/key/prefix without inference before reserving evidence.
    from benchmark_service_client import LocalServiceClient

    LocalServiceClient(host, effective_env["MEM0_EVAL_API_KEY"], effective_env["MEM0_EVAL_SCOPE_PREFIX"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x") as stream:
        json.dump({"plan": plan, "overlay_sha256": overlay_sha256, "predict_only": predict_only,
                   "history_bounds": {"chunks": max_chunks, "characters": max_characters},
                   "scope_sha256": hashlib.sha256(effective_env["MEM0_EVAL_SCOPE_PREFIX"].encode()).hexdigest(),
                   "quality_acceptance": "unproven"}, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    command = [sys.executable, "-m", "benchmarks.longmemeval.run", "--project-name", "bounded-product-local",
               "--dataset-path", str(Path(dataset_path).resolve()),
               "--question-id", question_id, "--predict-only" if predict_only else "--ingest-only", "--max-workers", "1",
               "--max-history-chunks", str(max_chunks), "--max-history-characters", str(max_characters),
               "--output-dir", str(output), "--mem0-host", host]
    if predict_only:
        from benchmark_async_backend import reconcile_ledger

        command += ["--mode", "retrieval", "--top-k", "10", "--top-k-cutoffs", "10"]
        control = supervise(command, Path(str(output) + ".control.json"), wall_seconds, effective_env)
        result = {"prediction_work_reconciled": False, "control": control, "plan": plan,
                  "quality_acceptance": "unproven", "error_type": None}
        try:
            ledger = reconcile_ledger(output / "_http_ledger.jsonl")
            prediction = json.loads((output / f"{question_id}.json").read_text())
            ingestion = json.loads((output / f"_ingestion_{question_id}.json").read_text())
            result["http_ledger"] = ledger
            result["prediction_work_reconciled"] = bool(
                control.get("child_exit_code") == 0 and control["state"] == "child_finished"
                and ledger["recorded_calls_reconciled"]
                and ledger["recorded_operations"] == ["add"] * plan["chunks"] + ["search"]
                and ingestion.get("total_pairs_processed") == plan["chunks"]
                and ingestion.get("total_pairs_failed") == 0 and ingestion.get("chunk_size") == 2
                and prediction.get("question_id") == question_id
                and prediction.get("user_id") == ingestion.get("user_id")
                and isinstance(prediction.get("retrieval", {}).get("search_results"), list)
                and "cutoff_results" not in prediction
            )
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result["error_type"] = type(error).__name__
        with Path(str(output) + ".reconciliation.json").open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return result
    return supervise_ingestion(command, output, plan, wall_seconds, effective_env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "scripts/evaluation/trajectory.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=float, default=300)
    parser.add_argument("--max-events", type=int, default=9)
    parser.add_argument("--max-adds", type=int, default=3)
    parser.add_argument("--retain-outputs", action="store_true")
    parser.add_argument("--allow-hf-downloads", action="store_true", help="Explicitly allow ancillary HF asset fetching")
    args = parser.parse_args()
    try:
        events = json.loads(args.dataset.read_text())
        validate_envelope(events, args.max_events, args.max_adds)
        if any(
            path.exists()
            for path in (args.output, Path(str(args.output) + ".control.json"), Path(str(args.output) + ".reconciliation.json"))
        ):
            raise FileExistsError("Existing trajectory artifacts cannot be reused")
        # Fail invalid configuration/storage policy before reserving the control report.
        spec = importlib.util.spec_from_file_location("bounded_trajectory_runner", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.isolated_config(json.loads(args.config.read_text()), args.output.resolve())
        env = dict(os.environ)
        if not args.allow_hf_downloads:
            env["HF_HUB_OFFLINE"] = "1"
            env["TRANSFORMERS_OFFLINE"] = "1"
        command = [
            sys.executable, str(RUNNER), "--config", str(args.config.resolve()),
            "--dataset", str(args.dataset.resolve()), "--output", str(args.output.resolve()),
        ]
        if args.retain_outputs:
            command.append("--retain-outputs")
        control = supervise(command, Path(str(args.output) + ".control.json"), args.wall_seconds, env)
        report = reconcile(args.output, events, control)
        report["hf_download_policy"] = "allowed" if args.allow_hf_downloads else "offline_requested"
        # This is not proof that every dependency obeys the offline variables, nor of BM25 coverage.
        with Path(str(args.output) + ".reconciliation.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps(report, indent=2))
        return 0 if report["trajectory_checks_passed"] else 1
    except Exception as exc:
        print(f"Bounded trajectory failed ({type(exc).__name__}); inspect partial artifacts", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
