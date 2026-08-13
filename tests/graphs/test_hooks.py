from datetime import datetime, timezone

from mem0.graphs.extractors import ExtractorIdentity
from mem0.graphs.hooks import RelationshipGraphWriteHook
from mem0.graphs.models import RelationshipCandidate, SourceKind
from mem0.graphs.outbox import (
    ProjectionEventOperation,
    ProjectionEventProducer,
    ProjectionEventStatus,
    SQLiteProjectionOutbox,
)


class StaticExtractor:
    identity = ExtractorIdentity(name="hook-test", version="1")

    def extract(self, memory_text):
        return [
            RelationshipCandidate(
                subject={"text": "Alice", "semantic_type": "PERSON"},
                predicate="works_at",
                object={"text": "Acme", "semantic_type": "ORG"},
                confidence=0.9,
            )
        ]


def test_write_hook_prepares_then_publishes_validated_add(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    hook = RelationshipGraphWriteHook(
        producer=ProjectionEventProducer(outbox=outbox, extractor=StaticExtractor()),
        event_id_factory=lambda: "fixed-id",
        clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    event_id = hook.prepare_add(
        collection_name="memories",
        memory_id="memory-1",
        memory_hash="hash-1",
        metadata={"user_id": "user-1", "app_id": "app-1", "role": "assistant"},
    )
    pending = outbox.get(event_id)
    hook.publish(event_id, memory_text="Alice works at Acme")
    ready = outbox.get(event_id)

    assert event_id == "graph-fixed-id"
    assert pending is not None and pending.status is ProjectionEventStatus.PENDING
    assert pending.intent.scope.user_id == "user-1"
    assert pending.intent.scope.app_id == "app-1"
    assert pending.intent.source_kind is SourceKind.ASSISTANT
    assert ready is not None and ready.status is ProjectionEventStatus.READY
    assert ready.payload is not None and len(ready.payload.relationships) == 1
    outbox.close()


def test_write_hook_update_and_delete_intents_preserve_hash_guards(tmp_path):
    outbox = SQLiteProjectionOutbox(tmp_path / "projection.sqlite3")
    hook = RelationshipGraphWriteHook(
        producer=ProjectionEventProducer(outbox=outbox, extractor=StaticExtractor()),
        event_id_factory=iter(("update-id", "delete-id")).__next__,
    )
    metadata = {"run_id": "run-1", "role": "system"}

    update_id = hook.prepare_update(
        collection_name="memories",
        memory_id="memory-1",
        previous_hash="old-hash",
        memory_hash="new-hash",
        metadata=metadata,
    )
    delete_id = hook.prepare_delete(
        collection_name="memories",
        memory_id="memory-1",
        memory_hash="new-hash",
        metadata=metadata,
    )
    hook.publish(delete_id)

    update = outbox.get(update_id)
    deletion = outbox.get(delete_id)
    assert update is not None and update.intent.operation is ProjectionEventOperation.UPDATE
    assert update.intent.previous_hash == "old-hash"
    assert update.intent.source_kind is SourceKind.SYSTEM
    assert deletion is not None and deletion.status is ProjectionEventStatus.READY
    assert deletion.intent.operation is ProjectionEventOperation.DELETE
    outbox.close()
