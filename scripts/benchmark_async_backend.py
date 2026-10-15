"""Serial async product backend for the pinned suite's add/search call sites.

Not automatically selected by the suite. Search omits rerank only under a
declared service-default protocol; explicit rerank values remain unsupported.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import uuid
from pathlib import Path

from benchmark_compat import normalize_source_dates
from benchmark_service_client import LocalServiceClient


def reconcile_ledger(path):
    """Audit recorded dispatches, not quality or unrecorded planned workload."""
    started, finished = {}, {}
    errors = []
    for index, line in enumerate(Path(path).read_text().splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append({"line": index, "error": "malformed_record"})
            break
        if not isinstance(row, dict) or not isinstance(row.get("call_id"), str) or not row["call_id"]:
            errors.append({"line": index, "error": "invalid_call_id"})
            continue
        phase, call_id = row.get("phase"), row["call_id"]
        if phase == "started":
            if call_id in started:
                errors.append({"line": index, "error": "duplicate_start"})
            else:
                started[call_id] = row
        elif phase == "finished":
            if call_id not in started or call_id in finished:
                errors.append({"line": index, "error": "orphan_or_duplicate_finish"})
            else:
                finished[call_id] = row
                if any(row.get(key) != started[call_id].get(key)
                       for key in ("operation", "input_sha256", "scope_sha256")):
                    errors.append({"line": index, "error": "lineage_mismatch"})
        else:
            errors.append({"line": index, "error": "invalid_phase"})
    pending = sorted(set(started) - set(finished))
    unsuccessful = sorted(call_id for call_id, row in finished.items()
                          if row.get("outcome") != "returned" or not isinstance(row.get("http"), dict)
                          or row["http"].get("status") != "success")
    return {"recorded_starts": len(started), "recorded_finishes": len(finished),
            "recorded_operations": sorted(row.get("operation", "") for row in started.values()),
            "pending_call_ids": pending, "unsuccessful_or_unknown_call_ids": unsuccessful,
            "errors": errors, "recorded_calls_reconciled": bool(started) and not (pending or unsuccessful or errors),
            "planned_workload_complete": "unproven", "quality_acceptance": "unproven"}


def reconcile_ingestion_stage(output, plan, control):
    """Compare independently supplied work plan with recorded ingestion evidence."""
    if (not isinstance(plan, dict) or not isinstance(plan.get("question_id"), str)
            or isinstance(plan.get("chunks"), bool) or not isinstance(plan.get("chunks"), int)
            or not 1 <= plan["chunks"] <= 100):
        raise ValueError("Require a validated single-history ingestion plan")
    directory = Path(output)
    errors = []
    summary = ledger = None
    try:
        summary = json.loads((directory / "_ingestion_stage.json").read_text())
        ledger = reconcile_ledger(directory / "_http_ledger.jsonl")
    except (OSError, ValueError, TypeError):
        errors.append("missing_or_malformed_stage_evidence")
    outcomes = summary.get("outcomes") if isinstance(summary, dict) else None
    verified = (
        not errors and control.get("state") == "child_finished" and control.get("child_exit_code") == 0
        and isinstance(summary, dict) and summary.get("state") == "ingestion_completed"
        and isinstance(outcomes, list) and len(outcomes) == 1 and isinstance(outcomes[0], dict)
        and outcomes[0].get("question_id") == plan["question_id"] and outcomes[0].get("success") is True
        and outcomes[0].get("pairs") == plan["chunks"]
        and isinstance(ledger, dict) and ledger["recorded_calls_reconciled"]
        and ledger["recorded_starts"] == plan["chunks"]
        and ledger["recorded_operations"] == ["add"] * plan["chunks"]
    )
    return {"ingestion_work_reconciled": bool(verified), "plan": plan, "stage": summary,
            "http_ledger": ledger, "errors": errors, "quality_acceptance": "unproven",
            "scope": "Recorded ingestion count only; source provenance and server state need separate verification"}


def validate_checkpoint_resume(ledger_path, expected_requests, checkpoint, user_id, saved_context, current_context):
    """Authorize no dispatch: validate only an unambiguous successful source prefix.

    Context must be independently constructed from source/code and verified runtime
    identity. Matching caller-supplied fingerprints alone do not prove live state.
    Search-stage interruption and successful writes absent from the checkpoint
    require separate investigation; this helper never retries or repairs evidence.
    """
    required = {"dataset_sha256", "runtime_fingerprint", "runner_sha256"}
    if (not isinstance(saved_context, dict) or not isinstance(current_context, dict)
            or not required.issubset(saved_context) or saved_context != current_context
            or any(not isinstance(saved_context[key], str) or not saved_context[key] for key in required)):
        raise ValueError("Resume provenance changed or is incomplete")
    if (not isinstance(checkpoint, dict) or checkpoint.get("chunk_size") != 2
            or checkpoint.get("user_id") != user_id or not isinstance(expected_requests, list)
            or not expected_requests):
        raise ValueError("Invalid resume checkpoint or source plan")
    keys = [row.get("chunk_key") for row in expected_requests if isinstance(row, dict)]
    if (len(keys) != len(expected_requests) or any(not isinstance(key, str) or not key for key in keys)
            or len(set(keys)) != len(keys)):
        raise ValueError("Invalid source chunk identities")
    completed = checkpoint.get("completed_chunks")
    if (not isinstance(completed, list) or not completed
            or any(not isinstance(key, str) for key in completed)
            or len(set(completed)) != len(completed) or set(completed) != set(keys[:len(completed)])):
        raise ValueError("Checkpoint is not a unique contiguous source prefix")
    report = reconcile_ledger(ledger_path)
    if (not report["recorded_calls_reconciled"] or report["recorded_starts"] != len(completed)
            or report["recorded_operations"] != ["add"] * len(completed)):
        raise ValueError("Ledger has ambiguous, failed or uncheckpointed operations")
    starts = [row for row in map(json.loads, Path(ledger_path).read_text().splitlines())
              if row.get("phase") == "started"]
    for observed, expected in zip(starts, expected_requests):
        if any(observed.get(key) != expected.get(key) or not isinstance(expected.get(key), str)
               or not expected[key] for key in ("input_sha256", "scope_sha256")):
            raise ValueError("Ledger input/scope differs from source prefix")
    return len(completed)


class ProductBackend:
    def __init__(self, base_url, api_key, scope_prefix, timeout=30, ledger_path=None, resume_validation=None):
        self._client = LocalServiceClient(base_url, api_key, scope_prefix, timeout)
        self._lock = None
        self._ledger = None
        if ledger_path is not None:
            path = Path(ledger_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ledger = path.open("r+" if resume_validation is not None else "x")
            try:
                fcntl.flock(self._ledger.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if resume_validation is not None:
                    validate_checkpoint_resume(path, **resume_validation)
                    self._ledger.seek(0, 2)
            except BaseException:
                self._ledger.close()
                raise

    def _record(self, row):
        if self._ledger is not None:
            self._ledger.write(json.dumps(row, sort_keys=True) + "\n")
            self._ledger.flush()
            os.fsync(self._ledger.fileno())

    @property
    def scope_prefix(self):
        return self._client.prefix

    async def _call(self, operation, request):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            call_id = uuid.uuid4().hex
            lineage = {"call_id": call_id, "operation": operation,
                       "input_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
                       "scope_sha256": hashlib.sha256(request["user_id"].encode()).hexdigest()}
            self._record({**lineage, "phase": "started", "outcome": "pending"})
            task = asyncio.create_task(asyncio.to_thread(self._client.call, operation, request))
            outcome = "failed_or_interrupted"
            try:
                result = await asyncio.shield(task)
                outcome = "returned"
                return result
            except asyncio.CancelledError:
                # A cancelled coroutine does not cancel urllib/server execution.
                # Keep the serial slot until the transport finishes or fails.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if task.done() and not task.cancelled():
                    task.exception()  # Retrieve transport failure without hiding caller cancellation.
                raise
            finally:
                self._record({**lineage, "phase": "finished", "outcome": outcome,
                              "http": self._client.last_evidence, "quality_acceptance": "unproven"})

    async def add(self, messages, user_id, observation_date=None, timestamp=None,
                  custom_instructions=None, metadata=None):
        return await self._call("add", {
            "messages": messages, "user_id": user_id, "observation_date": observation_date,
            "timestamp": timestamp, "custom_instructions": custom_instructions, "metadata": metadata,
        })

    async def search(self, query, user_id, top_k=200, rerank=None, score_debug=False):
        if rerank is not None or score_debug is not False:
            raise ValueError("Product search cannot express rerank or score-debug options")
        result = normalize_source_dates(await self._call("search", {"query": query, "user_id": user_id, "limit": top_k}))
        return result["results"]

    async def delete_user(self, user_id):
        raise ValueError("Destructive cleanup is not authorized by this backend; provision fresh isolated storage")

    async def close(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._ledger is not None:
                self._ledger.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
