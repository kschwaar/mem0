from unittest.mock import MagicMock

import pytest

from mem0.graphs.models import GraphMemoryState, GraphScope
from mem0.graphs.neo4j import INSPECT_MEMORY_QUERY, Neo4jGraphConfig, Neo4jSchemaAdapter


class FakeResult:
    def __init__(self, record):
        self.record = record

    def single(self, *, strict=False):
        return self.record


class FakeTransaction:
    def __init__(self, record):
        self.record = record
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return FakeResult(self.record)


def inspection_record(**overrides):
    record = {
        "memory_count": 1,
        "memory_hash": "hash-1",
        "evidence_count": 1,
        "linked_evidence_count": 1,
        "assertion_count": 1,
        "complete_assertion_count": 1,
    }
    record.update(overrides)
    return record


def adapter_and_transaction(record):
    driver = MagicMock()
    session = MagicMock()
    transaction = FakeTransaction(record)
    driver.session.return_value.__enter__.return_value = session
    session.execute_read.side_effect = lambda callback, *args: callback(transaction, *args)
    config = Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password")
    return Neo4jSchemaAdapter(config, driver), driver, session, transaction


@pytest.mark.parametrize(
    ("record", "memory_hash", "expected"),
    [
        (inspection_record(memory_count=0, memory_hash=None), "hash-1", GraphMemoryState.MISSING),
        (inspection_record(), "hash-1", GraphMemoryState.CURRENT),
        (inspection_record(), "hash-2", GraphMemoryState.CONFLICT),
        (inspection_record(memory_hash=None), "hash-1", GraphMemoryState.INCOMPLETE),
        (inspection_record(memory_count=2, memory_hash=None), "hash-1", GraphMemoryState.INCOMPLETE),
        (inspection_record(evidence_count=0, linked_evidence_count=0), "hash-1", GraphMemoryState.INCOMPLETE),
        (
            inspection_record(linked_evidence_count=0, assertion_count=0, complete_assertion_count=0),
            "hash-1",
            GraphMemoryState.INCOMPLETE,
        ),
        (inspection_record(complete_assertion_count=0), "hash-1", GraphMemoryState.INCOMPLETE),
    ],
)
def test_inspect_classifies_database_summary(record, memory_hash, expected):
    adapter, _, _, _ = adapter_and_transaction(record)

    state = adapter.inspect(
        collection_name="memories",
        scope=GraphScope(user_id="user-1"),
        memory_id="memory-1",
        memory_hash=memory_hash,
    )

    assert state is expected


def test_inspect_uses_a_parameterized_read_transaction_and_exact_scope():
    adapter, driver, session, transaction = adapter_and_transaction(inspection_record())
    scope = GraphScope(user_id="user-1", agent_id="agent-1")

    adapter.inspect(
        collection_name=" memories ",
        scope=scope,
        memory_id=" memory-1 ",
        memory_hash=" hash-1 ",
    )

    driver.session.assert_called_once_with(database="neo4j")
    session.execute_read.assert_called_once()
    assert transaction.calls == [
        (
            INSPECT_MEMORY_QUERY.strip(),
            {"collection_name": "memories", "scope_key": scope.key, "memory_id": "memory-1"},
        )
    ]
    assert INSPECT_MEMORY_QUERY.count(".scope_key = $scope_key") == 3
    assert INSPECT_MEMORY_QUERY.count(".collection_name = $collection_name") == 3


@pytest.mark.parametrize(("field", "value"), [("collection_name", " "), ("memory_id", ""), ("memory_hash", " ")])
def test_inspect_rejects_empty_identity_values_before_opening_session(field, value):
    adapter, driver, _, _ = adapter_and_transaction(inspection_record())
    arguments = {
        "collection_name": "memories",
        "scope": GraphScope(user_id="user-1"),
        "memory_id": "memory-1",
        "memory_hash": "hash-1",
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        adapter.inspect(**arguments)

    driver.session.assert_not_called()


def test_inspect_rejects_closed_adapter():
    adapter, driver, _, _ = adapter_and_transaction(inspection_record())
    adapter.close()

    with pytest.raises(RuntimeError, match="closed"):
        adapter.inspect(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            memory_id="memory-1",
            memory_hash="hash-1",
        )

    driver.session.assert_not_called()
