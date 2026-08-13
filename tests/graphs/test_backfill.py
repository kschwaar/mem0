import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from mem0.graphs.backfill import (
    BackfillCheckpoint,
    BackfillFailureStage,
    BackfillRunRequest,
    BackfillStatus,
    CanonicalMemory,
    CanonicalMemoryPage,
    GraphMemoryState,
    JsonBackfillCheckpointStore,
    RelationshipGraphBackfillRunner,
)
from mem0.graphs.extractors import ExtractorIdentity, RelationshipExtractionError
from mem0.graphs.models import ProjectionResult, SourceKind
from mem0.graphs.service import MemoryGraphProjectionResult, ProjectionVerificationError


def memory(memory_id, text=None, memory_hash=None):
    return CanonicalMemory(
        memory_id=memory_id,
        memory_text=text or f"text for {memory_id}",
        memory_hash=memory_hash or f"hash-{memory_id}",
        source_kind=SourceKind.USER,
    )


def request(**overrides):
    values = {
        "run_id": "run-1",
        "collection_name": "memories",
        "scope": {"user_id": "user-1"},
        "page_size": 2,
    }
    values.update(overrides)
    return BackfillRunRequest(**values)


class FakeReader:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.calls = []

    def page(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.pages[kwargs["cursor"]]


class FakeInspector:
    def __init__(self, states=None, errors=None):
        self.states = states or {}
        self.errors = errors or {}
        self.calls = []

    def inspect(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["memory_id"] in self.errors:
            raise self.errors[kwargs["memory_id"]]
        return self.states.get(kwargs["memory_id"], GraphMemoryState.MISSING)


class FakeProjector:
    extractor_identity = ExtractorIdentity(name="static", version="1", model_id="test-model")

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def project(self, projection_request):
        self.calls.append(projection_request)
        outcome = self.outcomes.get(projection_request.memory_id, "projected")
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "empty":
            return MemoryGraphProjectionResult(projections=(), provenance=())
        return MemoryGraphProjectionResult(
            projections=(
                ProjectionResult(
                    memory_id=projection_request.memory_id,
                    subject_entity_id=UUID("11111111-1111-5111-8111-111111111111"),
                    object_entity_id=UUID("22222222-2222-5222-8222-222222222222"),
                    assertion_id=UUID("33333333-3333-5333-8333-333333333333"),
                    evidence_id=UUID("44444444-4444-5444-8444-444444444444"),
                ),
            ),
            provenance=(),
        )


class MemoryCheckpointStore:
    def __init__(self):
        self.values = {}
        self.saves = []

    def load(self, run_id):
        return self.values.get(run_id)

    def save(self, checkpoint):
        self.values[checkpoint.run_id] = checkpoint
        self.saves.append(checkpoint)


def runner(*, pages=None, reader_error=None, states=None, inspector_errors=None, outcomes=None, store=None):
    reader = FakeReader(pages=pages, error=reader_error)
    inspector = FakeInspector(states=states, errors=inspector_errors)
    projector = FakeProjector(outcomes=outcomes)
    checkpoint_store = store or MemoryCheckpointStore()
    return (
        RelationshipGraphBackfillRunner(
            reader=reader,
            inspector=inspector,
            projector=projector,
            checkpoints=checkpoint_store,
        ),
        reader,
        inspector,
        projector,
        checkpoint_store,
    )


def test_sequential_backfill_pages_and_checkpoints_every_memory():
    pages = {
        None: CanonicalMemoryPage(memories=(memory("m1"), memory("m2")), next_cursor="page-2"),
        "page-2": CanonicalMemoryPage(memories=(memory("m3"),), next_cursor=None),
    }
    service, reader, inspector, projector, store = runner(pages=pages)

    checkpoint = service.run(request())

    assert checkpoint.status is BackfillStatus.COMPLETE
    assert checkpoint.processed == 3
    assert checkpoint.projected == 3
    assert checkpoint.completed_memory_ids == ("m1", "m2", "m3")
    assert [call["cursor"] for call in reader.calls] == [None, "page-2"]
    assert [call["memory_id"] for call in inspector.calls] == ["m1", "m2", "m3"]
    assert [call.memory_id for call in projector.calls] == ["m1", "m2", "m3"]
    assert all(call.projection_method.value == "BACKFILL" for call in projector.calls)
    assert len(store.saves) >= 5


def test_empty_and_failed_memories_do_not_prevent_later_memories():
    private_text = "private memory contents"
    pages = {
        None: CanonicalMemoryPage(
            memories=(memory("empty"), memory("bad", text=private_text), memory("good")),
            next_cursor=None,
        )
    }
    outcomes = {
        "empty": "empty",
        "bad": RelationshipExtractionError(f"invalid extraction for {private_text}"),
    }
    service, _, _, projector, _ = runner(pages=pages, outcomes=outcomes)

    checkpoint = service.run(request(page_size=3))

    assert checkpoint.status is BackfillStatus.COMPLETE
    assert checkpoint.processed == 3
    assert checkpoint.empty == 1
    assert checkpoint.projected == 1
    assert checkpoint.failed == 1
    assert checkpoint.failures[0].stage is BackfillFailureStage.EXTRACT
    assert private_text not in checkpoint.failures[0].message
    assert [call.memory_id for call in projector.calls] == ["empty", "bad", "good"]


def test_resume_after_limit_skips_checkpointed_memories():
    pages = {None: CanonicalMemoryPage(memories=(memory("m1"), memory("m2")), next_cursor=None)}
    store = MemoryCheckpointStore()
    first_runner, _, _, first_projector, _ = runner(pages=pages, store=store)

    limited = first_runner.run(request(max_memories=1))

    assert limited.status is BackfillStatus.LIMIT_REACHED
    assert limited.completed_memory_ids == ("m1",)
    assert [call.memory_id for call in first_projector.calls] == ["m1"]

    resumed_runner, resumed_reader, _, resumed_projector, _ = runner(pages=pages, store=store)
    completed = resumed_runner.run(request())

    assert completed.status is BackfillStatus.COMPLETE
    assert completed.completed_memory_ids == ("m1", "m2")
    assert [call["cursor"] for call in resumed_reader.calls] == [None]
    assert [call.memory_id for call in resumed_projector.calls] == ["m2"]


def test_reconciliation_skips_current_and_reports_conflicts_without_projection():
    pages = {
        None: CanonicalMemoryPage(
            memories=(memory("current"), memory("conflict"), memory("incomplete"), memory("missing")),
            next_cursor=None,
        )
    }
    states = {
        "current": GraphMemoryState.CURRENT,
        "conflict": GraphMemoryState.CONFLICT,
        "incomplete": GraphMemoryState.INCOMPLETE,
    }
    service, _, _, projector, _ = runner(pages=pages, states=states)

    checkpoint = service.run(request(page_size=4))

    assert checkpoint.skipped == 1
    assert checkpoint.conflicts == 2
    assert checkpoint.failed == 2
    assert checkpoint.projected == 1
    assert [call.memory_id for call in projector.calls] == ["missing"]
    assert {failure.memory_id for failure in checkpoint.failures} == {"conflict", "incomplete"}


@pytest.mark.parametrize(
    ("error", "stage"),
    [
        (ProjectionVerificationError("not visible"), BackfillFailureStage.VERIFY),
        (RuntimeError("neo4j unavailable"), BackfillFailureStage.PROJECT),
    ],
)
def test_projection_failures_are_classified(error, stage):
    pages = {None: CanonicalMemoryPage(memories=(memory("m1"), memory("m2")), next_cursor=None)}
    service, _, _, _, _ = runner(pages=pages, outcomes={"m1": error})

    checkpoint = service.run(request())

    assert checkpoint.failed == 1
    assert checkpoint.failures[0].stage is stage
    assert checkpoint.projected == 1


def test_reader_and_inspector_failures_are_checkpointed():
    service, _, _, _, store = runner(reader_error=RuntimeError("reader unavailable"))
    read_failure = service.run(request())
    assert read_failure.failures[0].stage is BackfillFailureStage.READ
    assert store.load("run-1") == read_failure

    pages = {None: CanonicalMemoryPage(memories=(memory("m1"), memory("m2")), next_cursor=None)}
    service, _, _, projector, _ = runner(
        pages=pages,
        inspector_errors={"m1": RuntimeError("inspection failed")},
    )
    reconcile_failure = service.run(request(run_id="run-2"))
    assert reconcile_failure.failures[0].stage is BackfillFailureStage.RECONCILE
    assert reconcile_failure.projected == 1
    assert [call.memory_id for call in projector.calls] == ["m2"]


def test_complete_checkpoint_is_a_noop_and_resume_contract_is_immutable():
    pages = {None: CanonicalMemoryPage(memories=(memory("m1"),), next_cursor=None)}
    store = MemoryCheckpointStore()
    first_runner, _, _, _, _ = runner(pages=pages, store=store)
    completed = first_runner.run(request())

    second_runner, reader, _, projector, _ = runner(pages=pages, store=store)
    assert second_runner.run(request()) == completed
    assert reader.calls == []
    assert projector.calls == []

    with pytest.raises(ValueError, match="does not match"):
        second_runner.run(request(collection_name="other"))


def test_json_checkpoint_store_round_trips_atomically_without_memory_text(tmp_path):
    private_text = "private memory contents"
    store = JsonBackfillCheckpointStore(tmp_path)
    checkpoint = BackfillCheckpoint(
        run_id="safe-run_1",
        collection_name="memories",
        scope={"user_id": "user-1"},
        extractor_name="static",
        extractor_version="1",
        completed_memory_ids=("m1",),
        failures=(
            {
                "memory_id": "m1",
                "stage": "EXTRACT",
                "error_type": "RelationshipExtractionError",
                "message": "invalid extraction [REDACTED]",
            },
        ),
    )

    store.save(checkpoint)

    assert store.load("safe-run_1") == checkpoint
    serialized = store.path_for("safe-run_1").read_text(encoding="utf-8")
    assert private_text not in serialized
    assert json.loads(serialized)["scope"] == {
        "agent_id": None,
        "app_id": None,
        "run_id": None,
        "user_id": "user-1",
    }
    assert list(Path(tmp_path).glob("*.tmp")) == []


@pytest.mark.parametrize("run_id", ["../escape", "bad/name", "", "has space"])
def test_json_checkpoint_store_rejects_unsafe_run_ids(tmp_path, run_id):
    store = JsonBackfillCheckpointStore(tmp_path)
    with pytest.raises((ValueError, ValidationError)):
        store.load(run_id)


def test_checkpoint_failure_serialization_never_contains_failed_memory_text(tmp_path):
    private_text = "uniquely private memory text"
    pages = {None: CanonicalMemoryPage(memories=(memory("m1", text=private_text),), next_cursor=None)}
    store = JsonBackfillCheckpointStore(tmp_path)
    service, _, _, _, _ = runner(
        pages=pages,
        outcomes={"m1": RuntimeError(f"projection rejected {private_text}")},
        store=store,
    )

    checkpoint = service.run(request())

    assert private_text not in checkpoint.failures[0].message
    assert private_text not in store.path_for("run-1").read_text(encoding="utf-8")
