"""Async boundary tests without network or model inference."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from benchmark_async_backend import (  # noqa: E402
    ProductBackend,
    reconcile_ingestion_stage,
    reconcile_ledger,
)


def backend():
    return ProductBackend("http://127.0.0.1:8765", "synthetic", "evaluation-unique-001-")


def test_checkpoint_resume_matches_successful_ledger_and_contiguous_source_prefix(tmp_path):
    import json

    from benchmark_async_backend import validate_checkpoint_resume

    path = tmp_path / "ledger.jsonl"
    expected = [{"chunk_key": "s0_p0", "input_sha256": "input-a", "scope_sha256": "scope"},
                {"chunk_key": "s0_p1", "input_sha256": "input-b", "scope_sha256": "scope"}]
    start = {"call_id": "one", "operation": "add", "phase": "started",
             "input_sha256": "input-a", "scope_sha256": "scope"}
    finish = {**start, "phase": "finished", "outcome": "returned", "http": {"status": "success"}}
    checkpoint = {"chunk_size": 2, "completed_chunks": ["s0_p0"], "user_id": "scope-user"}
    context = {"dataset_sha256": "source", "runtime_fingerprint": "runtime", "runner_sha256": "runner"}

    def write(rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    write([start, finish])
    assert validate_checkpoint_resume(path, expected, checkpoint, "scope-user", context, context) == 1
    for bad_checkpoint in [{**checkpoint, "completed_chunks": []},
                           {**checkpoint, "completed_chunks": ["s0_p1"]},
                           {**checkpoint, "completed_chunks": ["s0_p0", "s0_p0"]},
                           {**checkpoint, "user_id": "other"},
                           {**checkpoint, "chunk_size": 1}]:
        with pytest.raises(ValueError):
            validate_checkpoint_resume(path, expected, bad_checkpoint, "scope-user", context, context)
    with pytest.raises(ValueError):
        validate_checkpoint_resume(path, expected, checkpoint, "scope-user", context,
                                   {**context, "runtime_fingerprint": "changed"})
    for rows in [[start], [start, {**finish, "outcome": "failed_or_interrupted"}],
                 [start, {**finish, "input_sha256": "wrong"}],
                 [{**start, "operation": "search"}, {**finish, "operation": "search"}]]:
        write(rows)
        with pytest.raises(ValueError):
            validate_checkpoint_resume(path, expected, checkpoint, "scope-user", context, context)


def test_add_and_search_use_transport_and_keep_failure_visible():
    async def scenario():
        subject = backend()
        subject._client.call = Mock(return_value={"results": []})
        await subject.add([{"role": "user", "content": "tea"}], "evaluation-unique-001-a", timestamp=0)
        assert subject._client.call.call_args.args[1]["timestamp"] == 0
        assert await subject.search("tea", "evaluation-unique-001-a", top_k=10) == []
        subject._client.call.side_effect = RuntimeError("failed")
        with pytest.raises(RuntimeError):
            await subject.search("tea", "evaluation-unique-001-a")
    asyncio.run(scenario())


def test_concurrent_calls_remain_serial():
    active, peak = 0, 0

    def transport(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.02)
        active -= 1
        return {"results": []}

    async def scenario():
        subject = backend()
        subject._client.call = transport
        await asyncio.gather(*(subject.search("tea", "evaluation-unique-001-a") for _ in range(3)))
    asyncio.run(scenario())
    assert peak == 1


def test_unsupported_options_and_cleanup_stop_before_transport():
    async def scenario():
        subject = backend()
        subject._client.call = Mock()
        with pytest.raises(ValueError):
            await subject.search("tea", "evaluation-unique-001-a", rerank=False)
        with pytest.raises(ValueError):
            await subject.delete_user("evaluation-unique-001-a")
        subject._client.call.assert_not_called()
    asyncio.run(scenario())


def test_repeated_cancellation_keeps_serial_slot_until_transport_finishes():
    import threading

    started, release = threading.Event(), threading.Event()
    calls = []

    def transport(*args):
        calls.append(args)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2)
        return {"results": []}

    async def scenario():
        subject = backend()
        subject._client.call = transport
        first = asyncio.create_task(subject.search("first", "evaluation-unique-001-a"))
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        second = asyncio.create_task(subject.search("second", "evaluation-unique-001-a"))
        try:
            await asyncio.sleep(0.02)
            assert len(calls) == 1 and not first.done() and not second.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await second == []
        assert len(calls) == 2
        await subject.close()
    asyncio.run(scenario())


def test_durable_ledger_has_lineage_but_no_credentials_or_content(tmp_path):
    import json

    path = tmp_path / "ledger.jsonl"

    async def scenario():
        subject = ProductBackend("http://127.0.0.1:8765", "credential-secret", "evaluation-unique-001-",
                                 ledger_path=path)
        subject._client.call = Mock(return_value={"results": []})
        await subject.add([{"role": "user", "content": "private-content"}], "evaluation-unique-001-a")
        await subject.close()
    asyncio.run(scenario())
    raw = path.read_text()
    rows = [json.loads(line) for line in raw.splitlines()]
    assert [row["phase"] for row in rows] == ["started", "finished"]
    assert rows[0]["call_id"] == rows[1]["call_id"]
    assert rows[1]["quality_acceptance"] == "unproven"
    assert "credential-secret" not in raw and "private-content" not in raw
    with pytest.raises(FileExistsError):
        ProductBackend("http://127.0.0.1:8765", "synthetic", "evaluation-unique-001-", ledger_path=path)


def test_backend_created_after_asyncio_run_can_be_used_in_a_new_loop():
    subject = backend()
    subject._client.call = Mock(return_value={"results": []})

    asyncio.run(subject.search("tea", "evaluation-unique-001-a"))
    assert asyncio.run(subject.search("coffee", "evaluation-unique-001-a")) == []
    assert subject._client.call.call_count == 2


def test_ledger_failure_stops_before_dispatch(tmp_path):
    async def scenario():
        subject = ProductBackend("http://127.0.0.1:8765", "synthetic", "evaluation-unique-001-",
                                 ledger_path=tmp_path / "ledger.jsonl")
        subject._client.call = Mock()
        await subject.close()
        with pytest.raises(ValueError):
            await subject.search("tea", "evaluation-unique-001-a")
        subject._client.call.assert_not_called()
    asyncio.run(scenario())


def test_ledger_reconciliation_requires_finished_success_and_preserved_lineage(tmp_path):
    import json

    path = tmp_path / "ledger.jsonl"
    start = {"call_id": "a", "phase": "started", "operation": "add", "input_sha256": "x", "scope_sha256": "y"}
    finish = {**start, "phase": "finished", "outcome": "returned", "http": {"status": "success"}}

    def write(rows):
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    write([start])
    assert reconcile_ledger(path)["pending_call_ids"] == ["a"]
    write([start, finish])
    assert reconcile_ledger(path)["recorded_calls_reconciled"]
    assert reconcile_ledger(path)["planned_workload_complete"] == "unproven"
    for rows in ([start, start, finish], [finish], [start, {**finish, "scope_sha256": "different"}]):
        write(rows)
        assert not reconcile_ledger(path)["recorded_calls_reconciled"]
    write([start, {**finish, "http": None}])
    assert reconcile_ledger(path)["unsuccessful_or_unknown_call_ids"] == ["a"]
    path.write_text(json.dumps(start) + '\n{"call_id":')
    assert reconcile_ledger(path)["errors"][0]["error"] == "malformed_record"


def test_ingestion_reconciliation_cannot_pass_interruption_or_plan_mismatch(tmp_path):
    import json

    start = {"call_id": "a", "phase": "started", "operation": "add"}
    finish = {**start, "phase": "finished", "outcome": "returned", "http": {"status": "success"}}
    (tmp_path / "_http_ledger.jsonl").write_text(json.dumps(start) + "\n" + json.dumps(finish) + "\n")
    (tmp_path / "_ingestion_stage.json").write_text(json.dumps({
        "state": "ingestion_completed", "outcomes": [{"question_id": "q", "success": True, "pairs": 1}],
    }))
    plan = {"question_id": "q", "chunks": 1}
    control = {"state": "child_finished", "child_exit_code": 0}
    assert reconcile_ingestion_stage(tmp_path, plan, control)["ingestion_work_reconciled"]
    assert not reconcile_ingestion_stage(tmp_path, {**plan, "chunks": 2}, control)["ingestion_work_reconciled"]
    assert not reconcile_ingestion_stage(tmp_path, plan, {**control, "state": "interrupted_wall_budget"})[
        "ingestion_work_reconciled"
    ]
