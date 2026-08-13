import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mem0.graphs.models import (
    GraphScope,
    RelationshipCandidate,
    SourceKind,
    excerpt_sha256,
)
from mem0.graphs.neo4j import (
    DELETE_MEMORY_EVIDENCE_QUERY,
    LIFECYCLE_TARGET_QUERY,
    MARK_MEMORY_DELETED_QUERY,
    PROJECT_RELATIONSHIP_QUERY,
    READ_PROJECTION_EVENT_QUERY,
    RECORD_PROJECTION_EVENT_QUERY,
    RETRACT_UNSUPPORTED_ASSERTIONS_QUERY,
    UPDATE_MEMORY_VERSION_QUERY,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
    ProjectionConflictError,
)
from mem0.graphs.outbox import (
    ProjectionEvent,
    ProjectionEventIntent,
    ProjectionEventOperation,
    ProjectionEventPayload,
    ProjectionEventStatus,
)

NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, record):
        self.record = record

    def single(self, *, strict=False):
        return self.record


class FakeTransaction:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        value = self.records.get(query)
        if isinstance(value, list):
            value = value.pop(0)
        return FakeResult(value)


def relationship():
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Acme", "semantic_type": "ORG"},
        confidence=0.9,
    )


def event(operation=ProjectionEventOperation.UPSERT, **overrides):
    intent_values = {
        "event_id": "event-1",
        "operation": operation,
        "collection_name": "memories",
        "scope": GraphScope(user_id="user-1"),
        "memory_id": "memory-1",
        "memory_hash": "new-hash" if operation is ProjectionEventOperation.UPDATE else "hash-1",
        "previous_hash": "old-hash" if operation is ProjectionEventOperation.UPDATE else None,
        "source_kind": SourceKind.USER,
        "occurred_at": NOW,
    }
    intent_values.update(overrides)
    payload = (
        ProjectionEventPayload()
        if operation is ProjectionEventOperation.DELETE
        else ProjectionEventPayload(
            relationships=(relationship(),),
            excerpt_hash=excerpt_sha256("Alice works at Acme"),
            extractor_name="test-extractor",
            extractor_version="1",
            model_id="test-model",
        )
    )
    return ProjectionEvent(
        intent=ProjectionEventIntent(**intent_values),
        status=ProjectionEventStatus.PROCESSING,
        payload=payload,
        attempts=1,
        available_at=NOW,
        lease_owner="worker",
        lease_expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def projection_record():
    return {
        "memory_id": "memory-1",
        "subject_entity_id": "11111111-1111-5111-8111-111111111111",
        "object_entity_id": "22222222-2222-5222-8222-222222222222",
        "assertion_id": "33333333-3333-5333-8333-333333333333",
        "evidence_id": "44444444-4444-5444-8444-444444444444",
    }


def existing_ledger(projection_event):
    intent = projection_event.intent
    canonical_payload = json.dumps(
        projection_event.payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "event_id": intent.event_id,
        "operation": intent.operation.value,
        "collection_name": intent.collection_name,
        "scope_key": intent.scope.key,
        "memory_id": intent.memory_id,
        "memory_hash": intent.memory_hash,
        "previous_hash": intent.previous_hash,
        "source_kind": intent.source_kind.value,
        "occurred_at": "2026-08-13T12:00:00Z",
        "payload_hash": hashlib.sha256(canonical_payload.encode()).hexdigest(),
    }


def adapter_with_records(records):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(records)
    driver.session.return_value.__enter__.return_value = session
    session.execute_write.side_effect = lambda callback, *args: callback(transaction, *args)
    config = Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password")
    return Neo4jSchemaAdapter(config, driver), session, transaction


def test_duplicate_delivery_is_skipped_by_graph_ledger():
    projection_event = event()
    adapter, session, transaction = adapter_with_records(
        {
            READ_PROJECTION_EVENT_QUERY.strip(): [None, existing_ledger(projection_event)],
            PROJECT_RELATIONSHIP_QUERY.strip(): projection_record(),
            RECORD_PROJECTION_EVENT_QUERY.strip(): {"event_id": "event-1"},
        }
    )

    first = adapter.apply(projection_event)
    duplicate = adapter.apply(projection_event)

    assert first.already_applied is False
    assert duplicate.already_applied is True
    assert session.execute_write.call_count == 2
    queries = [query for query, _ in transaction.calls]
    assert queries.count(PROJECT_RELATIONSHIP_QUERY.strip()) == 1
    assert queries.count(RECORD_PROJECTION_EVENT_QUERY.strip()) == 1
    assert queries[-1] == READ_PROJECTION_EVENT_QUERY.strip()


def test_graph_mutation_and_ledger_record_share_one_managed_transaction():
    adapter, session, transaction = adapter_with_records(
        {
            READ_PROJECTION_EVENT_QUERY.strip(): None,
            PROJECT_RELATIONSHIP_QUERY.strip(): projection_record(),
            RECORD_PROJECTION_EVENT_QUERY.strip(): {"event_id": "event-1"},
        }
    )

    adapter.apply(event())

    session.execute_write.assert_called_once()
    assert [query for query, _ in transaction.calls] == [
        READ_PROJECTION_EVENT_QUERY.strip(),
        PROJECT_RELATIONSHIP_QUERY.strip(),
        RECORD_PROJECTION_EVENT_QUERY.strip(),
    ]
    ledger_parameters = transaction.calls[-1][1]
    assert ledger_parameters["scope_key"] == GraphScope(user_id="user-1").key
    assert "relationships" not in ledger_parameters
    assert "Alice works at Acme" not in str(transaction.calls)


def test_update_event_uses_hash_guarded_replace_before_ledger_record():
    adapter, _, transaction = adapter_with_records(
        {
            READ_PROJECTION_EVENT_QUERY.strip(): None,
            LIFECYCLE_TARGET_QUERY.strip(): {
                "memory_id": "memory-1",
                "evidence_element_ids": ["old-evidence"],
                "assertion_element_ids": ["old-assertion"],
            },
            UPDATE_MEMORY_VERSION_QUERY.strip(): {"memory_id": "memory-1"},
            PROJECT_RELATIONSHIP_QUERY.strip(): projection_record(),
            DELETE_MEMORY_EVIDENCE_QUERY.strip(): {"evidence_deleted": 1},
            RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(): {"assertions_retracted": 1},
            RECORD_PROJECTION_EVENT_QUERY.strip(): {"event_id": "event-1"},
        }
    )

    result = adapter.apply(event(ProjectionEventOperation.UPDATE))

    assert result.operation is ProjectionEventOperation.UPDATE
    assert [query for query, _ in transaction.calls][-1] == RECORD_PROJECTION_EVENT_QUERY.strip()
    target_parameters = transaction.calls[1][1]
    assert target_parameters["memory_hash"] == "old-hash"


def test_delete_event_uses_hash_guarded_delete_before_ledger_record():
    adapter, _, transaction = adapter_with_records(
        {
            READ_PROJECTION_EVENT_QUERY.strip(): None,
            LIFECYCLE_TARGET_QUERY.strip(): {
                "memory_id": "memory-1",
                "evidence_element_ids": [],
                "assertion_element_ids": [],
            },
            MARK_MEMORY_DELETED_QUERY.strip(): {"memory_id": "memory-1"},
            RECORD_PROJECTION_EVENT_QUERY.strip(): {"event_id": "event-1"},
        }
    )

    result = adapter.apply(event(ProjectionEventOperation.DELETE))

    assert result.operation is ProjectionEventOperation.DELETE
    assert [query for query, _ in transaction.calls] == [
        READ_PROJECTION_EVENT_QUERY.strip(),
        LIFECYCLE_TARGET_QUERY.strip(),
        MARK_MEMORY_DELETED_QUERY.strip(),
        RECORD_PROJECTION_EVENT_QUERY.strip(),
    ]


def test_reused_event_id_with_different_mutation_fails_before_graph_write():
    projection_event = event()
    collision = existing_ledger(projection_event)
    collision["memory_hash"] = "other-hash"
    adapter, _, transaction = adapter_with_records({READ_PROJECTION_EVENT_QUERY.strip(): collision})

    with pytest.raises(ProjectionConflictError, match="different graph mutation"):
        adapter.apply(projection_event)

    assert [query for query, _ in transaction.calls] == [READ_PROJECTION_EVENT_QUERY.strip()]


def test_reused_event_id_with_different_payload_fails_before_graph_write():
    original = event()
    changed_relationship = RelationshipCandidate(
        **{
            **relationship().model_dump(),
            "object": {"text": "Beta", "semantic_type": "ORG"},
        }
    )
    changed = original.model_copy(
        update={"payload": original.payload.model_copy(update={"relationships": (changed_relationship,)})}
    )
    adapter, _, transaction = adapter_with_records(
        {READ_PROJECTION_EVENT_QUERY.strip(): existing_ledger(original)}
    )

    with pytest.raises(ProjectionConflictError, match="different graph mutation"):
        adapter.apply(changed)

    assert [query for query, _ in transaction.calls] == [READ_PROJECTION_EVENT_QUERY.strip()]
