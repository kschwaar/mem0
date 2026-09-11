from unittest.mock import MagicMock

from mem0.graphs.models import EntityReference, GraphScope
from mem0.graphs.neo4j import (
    READ_CANDIDATE_SIGNALS_QUERY,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
)


class FakeResult:
    def __init__(self, records):
        self.records = records

    def __iter__(self):
        return iter(self.records)


class FakeTransaction:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return FakeResult(self.records)


def record(assertion_id, confidence):
    return {
        "memory_id": "memory-1",
        "assertion_id": assertion_id,
        "subject": {
            "entity_id": "11111111-1111-5111-8111-111111111111",
            "normalized_name": "alice",
            "display_name": "Alice",
            "semantic_type": "PERSON",
        },
        "predicate": "works_at",
        "predicate_display": "works at",
        "object": {
            "entity_id": "22222222-2222-5222-8222-222222222222",
            "normalized_name": "acme",
            "display_name": "Acme",
            "semantic_type": "ORG",
        },
        "confidence": confidence,
    }


def adapter_with_records(records, *, query_factory=lambda query, timeout: query):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(records)
    driver.session.return_value.__enter__.return_value = session
    session.execute_read.side_effect = lambda callback, *args: callback(transaction, *args)
    config = Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password")
    return Neo4jSchemaAdapter(config, driver, query_factory=query_factory), transaction


def test_candidate_signals_aggregate_confidence_and_explanations_in_exact_scope():
    adapter, transaction = adapter_with_records(
        [
            record("33333333-3333-5333-8333-333333333333", 0.9),
            record("44444444-4444-5444-8444-444444444444", 0.7),
        ]
    )
    scope = GraphScope(user_id="user-1", app_id="app-1")

    signals = adapter.candidate_signals(
        collection_name="memories",
        scope=scope,
        query_entities=[EntityReference(text="Alice", semantic_type="PERSON")],
        candidate_memory_ids=["memory-1", "memory-2"],
        explanation_limit=2,
    )

    assert len(signals) == 1
    assert signals[0].memory_id == "memory-1"
    assert signals[0].graph_score == 0.9
    assert len(signals[0].explanations) == 2
    query, parameters = transaction.calls[0]
    assert query == READ_CANDIDATE_SIGNALS_QUERY.strip()
    assert parameters == {
        "collection_name": "memories",
        "scope_key": scope.key,
        "query_entities": [{"normalized_name": "alice", "semantic_type": "PERSON"}],
        "candidate_memory_ids": ["memory-1", "memory-2"],
        "explanation_limit": 2,
    }


def test_candidate_query_enforces_active_current_exact_scope_semantic_candidates():
    query = READ_CANDIDATE_SIGNALS_QUERY

    assert "assertion.state = 'ACTIVE'" in query
    assert "assertion.collection_name = $collection_name" in query
    assert "subject.scope_key = $scope_key" in query
    assert "object.scope_key = $scope_key" in query
    assert "memory.scope_key = $scope_key" in query
    assert "memory.memory_id = candidate_memory_id" in query
    assert "memory.deleted_at IS NULL" in query
    assert "evidence.memory_hash = memory.memory_hash" in query
    assert "LIMIT $explanation_limit" in query


def test_candidate_signals_skip_database_for_empty_inputs():
    adapter, transaction = adapter_with_records([])

    result = adapter.candidate_signals(
        collection_name="memories",
        scope=GraphScope(user_id="user-1"),
        query_entities=[],
        candidate_memory_ids=["memory-1"],
        explanation_limit=2,
    )

    assert result == []
    assert transaction.calls == []


def test_candidate_read_applies_configured_transaction_timeout():
    calls = []
    adapter, transaction = adapter_with_records([], query_factory=lambda query, timeout: calls.append(timeout) or query)

    adapter.candidate_signals(
        collection_name="memories",
        scope=GraphScope(user_id="user-1"),
        query_entities=[EntityReference(text="Alice", semantic_type="PERSON")],
        candidate_memory_ids=["memory-1"],
        explanation_limit=2,
    )

    callback = adapter._driver.session.return_value.__enter__.return_value.execute_read.call_args.args[0]
    assert callback.timeout == 0.25
    assert calls == []
    assert transaction.calls[0][0] == READ_CANDIDATE_SIGNALS_QUERY.strip()
