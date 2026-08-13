import hashlib
import time
from datetime import datetime, timezone

from mem0.graphs.operations import ProjectionOutboxReconciler, ProjectionWorkerService
from mem0.graphs.outbox import (
    ProjectionEventIntent,
    ProjectionEventOperation,
    ProjectionEventPayload,
    ProjectionEventStatus,
    SQLiteProjectionOutbox,
)
from mem0.graphs.models import GraphScope, SourceKind
from mem0.graphs.models import excerpt_sha256


class RecordingProducer:
    def __init__(self, outbox):
        self.outbox = outbox

    def publish(self, event_id, *, memory_text=None):
        return self.outbox.mark_ready(
            event_id,
            ProjectionEventPayload(
                excerpt_hash=excerpt_sha256(memory_text),
                extractor_name="test",
                extractor_version="1",
            ),
        )


class EmptyWorker:
    def __init__(self):
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return None


def pending(outbox, *, event_id, operation=ProjectionEventOperation.UPSERT, text="Alice"):
    return outbox.create_pending(
        ProjectionEventIntent(
            event_id=event_id,
            operation=operation,
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            memory_id=f"memory-{event_id}",
            memory_hash=hashlib.md5(text.encode()).hexdigest(),
            source_kind=SourceKind.USER,
            occurred_at=datetime.now(timezone.utc),
        )
    )


def test_worker_service_starts_stops_and_reports_queue_health(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "outbox.sqlite3")
    pending(outbox, event_id="pending")
    worker = EmptyWorker()
    service = ProjectionWorkerService(worker=worker, outbox=outbox, poll_seconds=0.01)

    service.start()
    time.sleep(0.03)
    health = service.health()
    service.stop()

    assert health.running is True
    assert health.outbox.pending == 1
    assert health.lag_seconds is not None
    assert worker.calls >= 1
    assert service.running is False
    outbox.close()


def test_reconciler_publishes_matching_pending_and_classifies_drift(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "outbox.sqlite3")
    pending(outbox, event_id="matching", text="Alice")
    pending(outbox, event_id="missing", text="Bob")
    pending(outbox, event_id="changed", text="Carol")
    reads = {
        "memory-matching": "Alice",
        "memory-missing": None,
        "memory-changed": "Different",
    }
    producer = RecordingProducer(outbox)

    report = ProjectionOutboxReconciler(
        outbox=outbox,
        producer=producer,
        read_memory_text=lambda intent: reads[intent.memory_id],
    ).reconcile()

    assert report.model_dump() == {
        "examined": 3,
        "published": 1,
        "missing": 1,
        "hash_mismatch": 1,
        "failed": 0,
    }
    assert outbox.get("matching").status is ProjectionEventStatus.READY
    outbox.close()
