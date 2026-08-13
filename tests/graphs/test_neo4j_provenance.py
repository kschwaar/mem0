from unittest.mock import MagicMock
from uuid import UUID

import pytest

from mem0.graphs.models import AssertionState, GraphScope, ProjectionMethod, SourceKind
from mem0.graphs.neo4j import (
    READ_PROVENANCE_BY_ASSERTION_QUERY,
    READ_PROVENANCE_BY_MEMORY_QUERY,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
)


ASSERTION_ID = UUID("6655adec-9b97-5f1c-a9ab-f4a98d4aa772")


def provenance_record(**overrides):
    values = {
        "collection_name": "memories",
        "scope_key": GraphScope(user_id="user-1").key,
        "assertion_id": str(ASSERTION_ID),
        "subject": {
            "entity_id": "4f2f14c4-d3b5-58dc-97be-3662d43227c8",
            "normalized_name": "alice",
            "display_name": "Alice",
            "semantic_type": "PERSON",
        },
        "predicate": "works_at",
        "predicate_display": "works at",
        "object": {
            "entity_id": "85c195b2-deaa-59f2-9911-7b174d85dd10",
            "normalized_name": "acme",
            "display_name": "Acme",
            "semantic_type": "ORG",
        },
        "state": "ACTIVE",
        "valid_from": None,
        "valid_to": None,
        "evidence_id": "801205f9-18ea-5422-a485-d27173dd5b73",
        "memory_id": "memory-1",
        "memory_hash": "hash-1",
        "excerpt_hash": "a" * 64,
        "confidence": 0.91,
        "observed_at": "2026-07-20T00:00:00Z",
        "recorded_at": "2026-07-21T00:00:00Z",
        "source_kind": "USER",
        "projection_method": "MANUAL",
        "extractor_name": "relationship-extractor",
        "extractor_version": "1",
        "model_id": "test-model",
    }
    values.update(overrides)
    return values


class FakeTransaction:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return iter(self.records)


def adapter_and_transaction(records):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(records)
    driver.session.return_value.__enter__.return_value = session
    session.execute_read.side_effect = lambda callback, *args: callback(transaction, *args)
    config = Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password")
    return Neo4jSchemaAdapter(config, driver), driver, session, transaction


def test_read_by_assertion_uses_exact_scope_and_returns_typed_provenance():
    adapter, driver, session, transaction = adapter_and_transaction([provenance_record()])
    scope = GraphScope(user_id="user-1")

    records = adapter.provenance_by_assertion(
        collection_name="memories",
        scope=scope,
        assertion=ASSERTION_ID,
    )

    driver.session.assert_called_once_with(database="neo4j")
    session.execute_read.assert_called_once()
    assert transaction.calls == [
        (
            READ_PROVENANCE_BY_ASSERTION_QUERY.strip(),
            {
                "collection_name": "memories",
                "scope_key": scope.key,
                "assertion_id": str(ASSERTION_ID),
            },
        )
    ]
    record = records[0]
    assert record.assertion_id == ASSERTION_ID
    assert record.subject.display_name == "Alice"
    assert record.object.semantic_type == "ORG"
    assert record.state is AssertionState.ACTIVE
    assert record.source_kind is SourceKind.USER
    assert record.projection_method is ProjectionMethod.MANUAL


def test_read_by_memory_returns_all_rows_in_database_order():
    second = provenance_record(
        assertion_id="77777777-7777-5777-8777-777777777777",
        evidence_id="88888888-8888-5888-8888-888888888888",
        predicate="located_in",
        recorded_at="2026-07-22T00:00:00Z",
    )
    adapter, _, _, transaction = adapter_and_transaction([provenance_record(), second])

    records = adapter.provenance_by_memory(
        collection_name=" memories ",
        scope=GraphScope(user_id="user-1"),
        memory_id=" memory-1 ",
    )

    assert [record.predicate for record in records] == ["works_at", "located_in"]
    assert transaction.calls[0][0] == READ_PROVENANCE_BY_MEMORY_QUERY.strip()
    assert transaction.calls[0][1]["collection_name"] == "memories"
    assert transaction.calls[0][1]["memory_id"] == "memory-1"


def test_empty_read_returns_empty_list():
    adapter, _, _, _ = adapter_and_transaction([])

    assert (
        adapter.provenance_by_memory(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            memory_id="missing",
        )
        == []
    )


def test_read_queries_enforce_scope_and_collection_on_every_scoped_node():
    for query in (READ_PROVENANCE_BY_ASSERTION_QUERY, READ_PROVENANCE_BY_MEMORY_QUERY):
        assert query.count(".scope_key = $scope_key") == 4
        assert query.count(".collection_name = $collection_name") == 4
        assert "ORDER BY evidence.recorded_at ASC, evidence.evidence_id ASC" in query


@pytest.mark.parametrize("collection_name", ["", "   "])
def test_read_rejects_empty_collection_before_opening_session(collection_name):
    adapter, driver, _, _ = adapter_and_transaction([])

    with pytest.raises(ValueError, match="collection_name"):
        adapter.provenance_by_memory(
            collection_name=collection_name,
            scope=GraphScope(user_id="user-1"),
            memory_id="memory-1",
        )

    driver.session.assert_not_called()


def test_read_rejects_empty_memory_id_and_invalid_assertion_id():
    adapter, driver, _, _ = adapter_and_transaction([])
    scope = GraphScope(user_id="user-1")

    with pytest.raises(ValueError, match="memory_id"):
        adapter.provenance_by_memory(collection_name="memories", scope=scope, memory_id=" ")
    with pytest.raises(ValueError):
        adapter.provenance_by_assertion(collection_name="memories", scope=scope, assertion="not-a-uuid")

    driver.session.assert_not_called()


def test_read_rejects_closed_adapter():
    adapter, driver, _, _ = adapter_and_transaction([])
    adapter.close()

    with pytest.raises(RuntimeError, match="closed"):
        adapter.provenance_by_memory(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            memory_id="memory-1",
        )

    driver.session.assert_not_called()
