from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mem0.graphs.models import GraphScope, ProjectionSource, RelationshipCandidate, SourceKind, excerpt_sha256
from mem0.graphs.neo4j import (
    DELETE_MEMORY_EVIDENCE_QUERY,
    LIFECYCLE_TARGET_QUERY,
    MARK_MEMORY_DELETED_QUERY,
    PROJECT_RELATIONSHIP_QUERY,
    RETRACT_UNSUPPORTED_ASSERTIONS_QUERY,
    UPDATE_MEMORY_VERSION_QUERY,
    GraphLifecycleConflictError,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
)


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


def source(**overrides):
    values = {
        "collection_name": "memories",
        "scope": GraphScope(user_id="user-1"),
        "memory_id": "memory-1",
        "memory_hash": "new-hash",
        "excerpt_hash": excerpt_sha256("Alice works at Beta"),
        "source_kind": SourceKind.USER,
        "extractor_name": "lifecycle-test",
        "extractor_version": "1",
        "recorded_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ProjectionSource(**values)


def relationship():
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Beta", "semantic_type": "ORG"},
        confidence=0.9,
    )


def projection_record():
    return {
        "memory_id": "memory-1",
        "subject_entity_id": "11111111-1111-5111-8111-111111111111",
        "object_entity_id": "22222222-2222-5222-8222-222222222222",
        "assertion_id": "33333333-3333-5333-8333-333333333333",
        "evidence_id": "44444444-4444-5444-8444-444444444444",
    }


def adapter_with_records(records):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(records)
    driver.session.return_value.__enter__.return_value = session
    session.execute_write.side_effect = lambda callback, *args: callback(transaction, *args)
    config = Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password")
    return Neo4jSchemaAdapter(config, driver), driver, transaction


def test_replace_relationships_is_one_hash_guarded_managed_transaction():
    target = {
        "memory_id": "memory-1",
        "evidence_element_ids": ["old-evidence"],
        "assertion_element_ids": ["old-assertion"],
    }
    adapter, driver, transaction = adapter_with_records(
        {
            LIFECYCLE_TARGET_QUERY.strip(): target,
            UPDATE_MEMORY_VERSION_QUERY.strip(): {"memory_id": "memory-1"},
            PROJECT_RELATIONSHIP_QUERY.strip(): projection_record(),
            DELETE_MEMORY_EVIDENCE_QUERY.strip(): {"evidence_deleted": 1},
            RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(): {"assertions_retracted": 1},
        }
    )

    result = adapter.replace_relationships([relationship()], source(), previous_hash="old-hash")

    driver.session.assert_called_once_with(database="neo4j")
    assert result.memory_id == "memory-1"
    assert result.evidence_deleted == 1
    assert result.assertions_retracted == 1
    assert len(result.projections) == 1
    assert [query for query, _ in transaction.calls] == [
        LIFECYCLE_TARGET_QUERY.strip(),
        UPDATE_MEMORY_VERSION_QUERY.strip(),
        PROJECT_RELATIONSHIP_QUERY.strip(),
        DELETE_MEMORY_EVIDENCE_QUERY.strip(),
        RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(),
    ]
    target_parameters = transaction.calls[0][1]
    assert target_parameters["collection_name"] == "memories"
    assert target_parameters["scope_key"] == source().scope.key
    assert target_parameters["memory_id"] == "memory-1"
    assert target_parameters["memory_hash"] == "old-hash"
    assert "memory_text" not in str(transaction.calls)
    assert "Alice works at Beta" not in str(transaction.calls)


def test_replace_with_empty_extraction_still_removes_old_evidence():
    adapter, _, transaction = adapter_with_records(
        {
            LIFECYCLE_TARGET_QUERY.strip(): {
                "memory_id": "memory-1",
                "evidence_element_ids": ["old-evidence"],
                "assertion_element_ids": ["old-assertion"],
            },
            UPDATE_MEMORY_VERSION_QUERY.strip(): {"memory_id": "memory-1"},
            DELETE_MEMORY_EVIDENCE_QUERY.strip(): {"evidence_deleted": 1},
            RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(): {"assertions_retracted": 1},
        }
    )

    result = adapter.replace_relationships([], source(), previous_hash="old-hash")

    assert result.projections == ()
    assert PROJECT_RELATIONSHIP_QUERY.strip() not in [query for query, _ in transaction.calls]
    assert result.evidence_deleted == 1
    assert result.assertions_retracted == 1


def test_delete_hard_deletes_only_target_evidence_and_tombstones_memory():
    adapter, _, transaction = adapter_with_records(
        {
            LIFECYCLE_TARGET_QUERY.strip(): {
                "memory_id": "memory-1",
                "evidence_element_ids": ["evidence-1", "evidence-2"],
                "assertion_element_ids": ["assertion-1"],
            },
            MARK_MEMORY_DELETED_QUERY.strip(): {"memory_id": "memory-1"},
            DELETE_MEMORY_EVIDENCE_QUERY.strip(): {"evidence_deleted": 2},
            RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(): {"assertions_retracted": 0},
        }
    )
    deleted_at = datetime(2026, 8, 13, tzinfo=timezone.utc)

    result = adapter.delete_memory(
        collection_name="memories",
        scope=GraphScope(user_id="user-1"),
        memory_id="memory-1",
        memory_hash="old-hash",
        deleted_at=deleted_at,
    )

    assert result.evidence_deleted == 2
    assert result.assertions_retracted == 0
    delete_parameters = next(
        parameters for query, parameters in transaction.calls if query == DELETE_MEMORY_EVIDENCE_QUERY.strip()
    )
    assert delete_parameters["evidence_element_ids"] == ["evidence-1", "evidence-2"]
    assert delete_parameters["scope_key"] == GraphScope(user_id="user-1").key


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_stale_or_wrong_scope_lifecycle_target_fails_before_mutation(operation):
    adapter, _, transaction = adapter_with_records({LIFECYCLE_TARGET_QUERY.strip(): None})

    with pytest.raises(GraphLifecycleConflictError, match="expected"):
        if operation == "update":
            adapter.replace_relationships([relationship()], source(), previous_hash="stale-hash")
        else:
            adapter.delete_memory(
                collection_name="memories",
                scope=GraphScope(user_id="wrong-user"),
                memory_id="memory-1",
                memory_hash="old-hash",
                deleted_at=datetime.now(timezone.utc),
            )

    assert [query for query, _ in transaction.calls] == [LIFECYCLE_TARGET_QUERY.strip()]


def test_lifecycle_rejects_closed_adapter_and_naive_delete_time():
    adapter, driver, _ = adapter_with_records({})

    with pytest.raises(ValueError, match="timezone"):
        adapter.delete_memory(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            memory_id="memory-1",
            memory_hash="old-hash",
            deleted_at=datetime(2026, 8, 13),
        )
    adapter.close()
    with pytest.raises(RuntimeError, match="closed"):
        adapter.replace_relationships([relationship()], source(), previous_hash="old-hash")

    driver.session.assert_not_called()


def test_projection_query_reactivates_an_assertion_supported_by_new_evidence():
    assert "ON MATCH SET" in PROJECT_RELATIONSHIP_QUERY
    assert "WHEN assertion.state = 'RETRACTED' THEN 'ACTIVE'" in PROJECT_RELATIONSHIP_QUERY
    assert "WHEN assertion.state = 'RETRACTED' THEN null" in PROJECT_RELATIONSHIP_QUERY
