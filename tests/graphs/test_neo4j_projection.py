from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from mem0.graphs.models import ProjectionSource, RelationshipCandidate, SourceKind, excerpt_sha256
from mem0.graphs.neo4j import (
    PROJECT_RELATIONSHIP_QUERY,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
    ProjectionConflictError,
)


def relationship():
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        predicate_display="works at",
        object={"text": "Acme", "semantic_type": "ORG"},
        confidence=0.91,
        observed_at="2026-07-20T00:00:00-06:00",
        valid_from="2026-07-01T00:00:00Z",
    )


def source(**overrides):
    values = {
        "collection_name": "memories",
        "scope": {"user_id": "user-1"},
        "memory_id": "memory-1",
        "memory_hash": "hash-1",
        "excerpt_hash": excerpt_sha256("Alice works at Acme"),
        "source_kind": SourceKind.USER,
        "extractor_name": "relationship-extractor",
        "extractor_version": "1",
        "model_id": "test-model",
        "recorded_at": "2026-07-21T00:00:00Z",
    }
    values.update(overrides)
    return ProjectionSource(**values)


class FakeResult:
    def __init__(self, record):
        self.record = record
        self.strict = None

    def single(self, *, strict=False):
        self.strict = strict
        return self.record


class FakeTransaction:
    def __init__(self, record_factory):
        self.record_factory = record_factory
        self.calls = []
        self.results = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        result = FakeResult(self.record_factory(parameters))
        self.results.append(result)
        return result


def adapter_and_transaction(record_factory):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(record_factory)
    driver.session.return_value.__enter__.return_value = session
    session.execute_write.side_effect = lambda callback, *args: callback(transaction, *args)
    graph_config = Neo4jGraphConfig(
        uri="neo4j://localhost:7687",
        username="neo4j",
        password="password",
    )
    return Neo4jSchemaAdapter(graph_config, driver), driver, session, transaction


def returned_ids(parameters):
    return {
        "memory_id": parameters["memory_id"],
        "subject_entity_id": parameters["subject_entity_id"],
        "object_entity_id": parameters["object_entity_id"],
        "assertion_id": parameters["assertion_id"],
        "evidence_id": parameters["evidence_id"],
    }


def test_projection_executes_one_parameterized_write_transaction():
    adapter, driver, session, transaction = adapter_and_transaction(returned_ids)

    result = adapter.project_relationship(relationship(), source())

    driver.session.assert_called_once_with(database="neo4j")
    session.execute_write.assert_called_once()
    assert len(transaction.calls) == 1
    query, parameters = transaction.calls[0]
    assert query == PROJECT_RELATIONSHIP_QUERY.strip()
    assert "$subject_display_name" in query
    assert "Alice" not in query
    assert parameters["subject_display_name"] == "Alice"
    assert parameters["object_display_name"] == "Acme"
    assert parameters["scope_key"] == source().scope.key
    assert parameters["observed_at"] == "2026-07-20T06:00:00Z"
    assert parameters["recorded_at"] == "2026-07-21T00:00:00Z"
    assert transaction.results[0].strict is False
    assert result.memory_id == "memory-1"
    assert isinstance(result.subject_entity_id, UUID)
    assert isinstance(result.object_entity_id, UUID)
    assert isinstance(result.assertion_id, UUID)
    assert isinstance(result.evidence_id, UUID)


def test_replaying_same_projection_uses_identical_query_and_parameters():
    adapter, _, _, transaction = adapter_and_transaction(returned_ids)
    candidate = relationship()
    projection_source = source()

    first = adapter.project_relationship(candidate, projection_source)
    second = adapter.project_relationship(candidate, projection_source)

    assert first == second
    assert transaction.calls[0] == transaction.calls[1]


def test_relationship_batch_uses_one_managed_transaction():
    adapter, _, session, transaction = adapter_and_transaction(returned_ids)
    first = relationship()
    second = RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="located_in",
        object={"text": "Denver", "semantic_type": "GPE"},
        confidence=0.8,
    )

    results = adapter.project_relationships([first, second], source())

    session.execute_write.assert_called_once()
    assert len(transaction.calls) == 2
    assert len(results) == 2
    assert results[0].assertion_id != results[1].assertion_id


def test_batch_conflict_raises_inside_the_single_managed_transaction():
    def conflict_on_second(parameters):
        if parameters["predicate"] == "located_in":
            return None
        return returned_ids(parameters)

    adapter, _, session, transaction = adapter_and_transaction(conflict_on_second)
    second = RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="located_in",
        object={"text": "Denver", "semantic_type": "GPE"},
        confidence=0.8,
    )

    with pytest.raises(ProjectionConflictError):
        adapter.project_relationships([relationship(), second], source())

    session.execute_write.assert_called_once()
    assert len(transaction.calls) == 2


def test_empty_batch_does_not_open_a_session():
    adapter, driver, _, _ = adapter_and_transaction(returned_ids)

    assert adapter.project_relationships([], source()) == []
    driver.session.assert_not_called()


def test_different_scope_produces_different_graph_identities():
    adapter, _, _, transaction = adapter_and_transaction(returned_ids)

    first = adapter.project_relationship(relationship(), source())
    second = adapter.project_relationship(relationship(), source(scope={"user_id": "user-2"}))

    assert first.subject_entity_id != second.subject_entity_id
    assert first.object_entity_id != second.object_entity_id
    assert first.assertion_id != second.assertion_id
    assert first.evidence_id != second.evidence_id
    assert transaction.calls[0][1]["scope_key"] != transaction.calls[1][1]["scope_key"]


def test_memory_hash_conflict_raises_inside_write_transaction():
    adapter, _, _, transaction = adapter_and_transaction(lambda parameters: None)

    with pytest.raises(ProjectionConflictError, match="different memory_hash"):
        adapter.project_relationship(relationship(), source())

    assert len(transaction.calls) == 1
    assert transaction.results[0].strict is False


def test_projection_rejects_closed_adapter_before_opening_session():
    adapter, driver, _, _ = adapter_and_transaction(returned_ids)
    adapter.close()

    with pytest.raises(RuntimeError, match="closed"):
        adapter.project_relationship(relationship(), source())

    driver.session.assert_not_called()


def test_projection_source_time_is_not_part_of_evidence_identity():
    adapter, _, _, _ = adapter_and_transaction(returned_ids)

    first = adapter.project_relationship(relationship(), source(recorded_at=datetime(2026, 7, 21, tzinfo=timezone.utc)))
    second = adapter.project_relationship(
        relationship(), source(recorded_at=datetime(2026, 7, 22, tzinfo=timezone.utc))
    )

    assert first.evidence_id == second.evidence_id
