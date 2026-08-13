from datetime import datetime, timedelta, timezone

import pytest

from mem0.graphs.extractors import ExtractorIdentity
from mem0.graphs.models import (
    GraphScope,
    RelationshipCandidate,
    SourceKind,
    excerpt_sha256,
)
from mem0.graphs.outbox import (
    ProjectionEventIntent,
    ProjectionEventOperation,
    ProjectionEventPayload,
    ProjectionEventProducer,
    ProjectionEventStatus,
    ProjectionOutboxConflictError,
    ProjectionOutboxLeaseError,
    ProjectionOutboxWorker,
    SQLiteProjectionOutbox,
)

NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
CLAIM_TIME = datetime.now(timezone.utc) + timedelta(days=1)


def relationship():
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Acme", "semantic_type": "ORG"},
        confidence=0.9,
    )


def intent(**overrides):
    values = {
        "event_id": "event-1",
        "operation": ProjectionEventOperation.UPSERT,
        "collection_name": "memories",
        "scope": GraphScope(user_id="user-1"),
        "memory_id": "memory-1",
        "memory_hash": "hash-1",
        "source_kind": SourceKind.USER,
        "occurred_at": NOW,
    }
    values.update(overrides)
    return ProjectionEventIntent(**values)


def payload():
    return ProjectionEventPayload(
        relationships=(relationship(),),
        excerpt_hash=excerpt_sha256("Alice works at Acme"),
        extractor_name="test-extractor",
        extractor_version="1",
        model_id="test-model",
    )


class FakeExtractor:
    identity = ExtractorIdentity(name="test-extractor", version="1", model_id="test-model")

    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def extract(self, memory_text):
        self.calls.append(memory_text)
        if self.error is not None:
            raise self.error
        return [relationship()]


class RecordingProjector:
    def __init__(self, *, failures=0):
        self.failures = failures
        self.events = []

    def apply(self, event):
        self.events.append(event)
        if len(self.events) <= self.failures:
            raise ConnectionError("private backend details")


def test_pending_intent_survives_restart_and_is_not_claimable(tmp_path):
    path = tmp_path / "state" / "projection.sqlite3"
    outbox = SQLiteProjectionOutbox(path)
    created = outbox.create_pending(intent())
    outbox.close()

    reopened = SQLiteProjectionOutbox(path)

    assert created.status is ProjectionEventStatus.PENDING
    assert reopened.get("event-1") == created
    assert reopened.claim(worker_id="worker", lease_seconds=30, now=NOW) is None
    reopened.close()


def test_ready_payload_is_immutable_and_claimed_atomically_across_connections(tmp_path):
    path = tmp_path / "projection.sqlite3"
    first = SQLiteProjectionOutbox(path)
    second = SQLiteProjectionOutbox(path)
    first.enqueue_ready(intent(), payload())

    claimed = first.claim(worker_id="worker-1", lease_seconds=30, now=CLAIM_TIME)

    assert claimed is not None
    assert claimed.status is ProjectionEventStatus.PROCESSING
    assert claimed.attempts == 1
    assert second.claim(worker_id="worker-2", lease_seconds=30, now=CLAIM_TIME) is None
    with pytest.raises(ProjectionOutboxConflictError, match="different payload"):
        second.enqueue_ready(intent(), ProjectionEventPayload(**{**payload().model_dump(), "relationships": ()}))
    with pytest.raises(ProjectionOutboxConflictError, match="different intent"):
        second.create_pending(intent(memory_hash="different-hash"))

    first.close()
    second.close()


def test_expired_lease_is_reclaimed_and_only_owner_can_finish(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    outbox.enqueue_ready(intent(), payload())
    outbox.claim(worker_id="crashed", lease_seconds=10, now=CLAIM_TIME)

    assert outbox.claim(worker_id="early", lease_seconds=10, now=CLAIM_TIME + timedelta(seconds=9)) is None
    reclaimed = outbox.claim(worker_id="replacement", lease_seconds=10, now=CLAIM_TIME + timedelta(seconds=10))

    assert reclaimed is not None
    assert reclaimed.attempts == 2
    with pytest.raises(ProjectionOutboxLeaseError):
        outbox.mark_applied("event-1", worker_id="crashed", now=CLAIM_TIME + timedelta(seconds=11))
    applied = outbox.mark_applied("event-1", worker_id="replacement", now=CLAIM_TIME + timedelta(seconds=11))
    assert applied.status is ProjectionEventStatus.APPLIED
    outbox.close()


def test_worker_retries_with_backoff_then_dead_letters_without_error_details(tmp_path):
    times = iter([CLAIM_TIME, CLAIM_TIME, CLAIM_TIME + timedelta(seconds=5), CLAIM_TIME + timedelta(seconds=5)])
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    outbox.enqueue_ready(intent(), payload())
    worker = ProjectionOutboxWorker(
        outbox=outbox,
        projector=RecordingProjector(failures=2),
        worker_id="worker",
        max_attempts=2,
        base_delay_seconds=5,
        clock=lambda: next(times),
    )

    retry = worker.run_once()
    dead = worker.run_once()

    assert retry is not None and retry.status is ProjectionEventStatus.RETRY
    assert retry.available_at == CLAIM_TIME + timedelta(seconds=5)
    assert dead is not None and dead.status is ProjectionEventStatus.DEAD_LETTER
    assert dead.attempts == 2
    assert dead.last_error_type == "ConnectionError"
    assert dead.last_error_message == "projection attempt failed"
    assert "private backend details" not in str(dead)
    outbox.close()


def test_dead_letter_can_be_replayed_and_applied(tmp_path):
    times = iter([CLAIM_TIME, CLAIM_TIME, CLAIM_TIME, CLAIM_TIME])
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    outbox.enqueue_ready(intent(), payload())
    failing = ProjectionOutboxWorker(
        outbox=outbox,
        projector=RecordingProjector(failures=1),
        worker_id="worker-1",
        max_attempts=1,
        clock=lambda: next(times),
    )
    assert failing.run_once().status is ProjectionEventStatus.DEAD_LETTER

    replayed = outbox.replay("event-1", now=CLAIM_TIME)
    successful = ProjectionOutboxWorker(
        outbox=outbox,
        projector=RecordingProjector(),
        worker_id="worker-2",
        clock=lambda: CLAIM_TIME,
    ).run_once()

    assert replayed.status is ProjectionEventStatus.READY
    assert successful is not None and successful.status is ProjectionEventStatus.APPLIED
    assert successful.attempts == 1
    outbox.close()


def test_producer_persists_intent_before_extraction_and_never_stores_raw_text(tmp_path):
    path = tmp_path / "projection.sqlite3"
    private_text = "Alice works at Acme; private token 7f83e2"
    extractor = FakeExtractor()
    outbox = SQLiteProjectionOutbox(path)

    event = ProjectionEventProducer(outbox=outbox, extractor=extractor).enqueue(intent(), memory_text=private_text)
    outbox.close()

    assert event.status is ProjectionEventStatus.READY
    assert event.payload is not None
    assert event.payload.excerpt_hash == excerpt_sha256(private_text)
    assert extractor.calls == [private_text]
    assert private_text.encode() not in path.read_bytes()


def test_extraction_failure_leaves_recoverable_pending_intent(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    producer = ProjectionEventProducer(outbox=outbox, extractor=FakeExtractor(error=RuntimeError("failed")))

    with pytest.raises(RuntimeError, match="failed"):
        producer.enqueue(intent(), memory_text="Alice works at Acme")

    event = outbox.get("event-1")
    assert event is not None and event.status is ProjectionEventStatus.PENDING
    assert event.payload is None
    outbox.close()


def test_delete_event_is_ready_without_extraction(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    extractor = FakeExtractor()
    delete_intent = intent(operation=ProjectionEventOperation.DELETE)

    event = ProjectionEventProducer(outbox=outbox, extractor=extractor).enqueue(delete_intent)

    assert event.status is ProjectionEventStatus.READY
    assert event.payload == ProjectionEventPayload()
    assert extractor.calls == []
    outbox.close()


def test_queue_stats_and_filtered_listing_are_privacy_safe(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    outbox.create_pending(intent(event_id="pending-event", memory_id="memory-2"))
    outbox.enqueue_ready(intent(), payload())

    stats = outbox.stats()
    ready = outbox.list_events(status=ProjectionEventStatus.READY, limit=1)

    assert stats.pending == 1
    assert stats.ready == 1
    assert stats.processing == stats.retry == stats.applied == stats.dead_letter == 0
    assert stats.oldest_unapplied_at is not None
    assert [event.intent.event_id for event in ready] == ["event-1"]
    assert "Alice works at Acme" not in stats.model_dump_json()
    outbox.close()


def test_claim_rejects_naive_clock_values(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    outbox.enqueue_ready(intent(), payload())

    with pytest.raises(ValueError, match="timezone"):
        outbox.claim(worker_id="worker", lease_seconds=30, now=datetime(2026, 8, 13, 12))

    outbox.close()
