from unittest.mock import MagicMock

from mem0.graphs.neo4j import (
    DELETE_COLLECTION_EVIDENCE_QUERY,
    DELETE_COLLECTION_NODES_QUERY,
    Neo4jGraphConfig,
    Neo4jSchemaAdapter,
)


class Result:
    def __init__(self, record):
        self.record = record

    def single(self):
        return self.record


class Transaction:
    def __init__(self):
        self.calls = []

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if query == DELETE_COLLECTION_EVIDENCE_QUERY.strip():
            return Result({"evidence_deleted": 2})
        return Result({"nodes_deleted": 5})


def test_reset_deletes_only_the_configured_collection_namespace():
    driver = MagicMock()
    session = MagicMock()
    transaction = Transaction()
    driver.session.return_value.__enter__.return_value = session
    session.execute_write.side_effect = lambda callback, *args: callback(transaction, *args)
    adapter = Neo4jSchemaAdapter(
        Neo4jGraphConfig(uri="neo4j://localhost:7687", username="neo4j", password="password"),
        driver,
    )

    deleted = adapter.reset_collection("personal-memories")

    assert deleted == 7
    assert [call[0] for call in transaction.calls] == [
        DELETE_COLLECTION_EVIDENCE_QUERY.strip(),
        DELETE_COLLECTION_NODES_QUERY.strip(),
    ]
    assert all(call[1] == {"collection_name": "personal-memories"} for call in transaction.calls)
    assert "$collection_name" in DELETE_COLLECTION_NODES_QUERY
    assert "MATCH (node)" in DELETE_COLLECTION_NODES_QUERY
