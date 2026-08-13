from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from mem0.graphs.neo4j import NEO4J_SCHEMA_OBJECT_NAMES, NEO4J_SCHEMA_STATEMENTS, Neo4jGraphConfig, Neo4jSchemaAdapter


def config(**overrides):
    values = {
        "uri": "neo4j://localhost:7687",
        "username": "neo4j",
        "password": "secret-password",
        "database": "neo4j",
    }
    values.update(overrides)
    return Neo4jGraphConfig(**values)


def driver_with_session():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    return driver, session


def test_config_accepts_neo4j_schemes_and_redacts_password():
    graph_config = config(uri="neo4j+s://example.databases.neo4j.io")

    assert graph_config.password.get_secret_value() == "secret-password"
    assert "secret-password" not in repr(graph_config)
    assert "secret-password" not in graph_config.model_dump_json()


@pytest.mark.parametrize("uri", ["https://localhost:7474", "localhost:7687", "file:///tmp/neo4j"])
def test_config_rejects_unsupported_uri_schemes(uri):
    with pytest.raises(ValidationError, match="Neo4j URI"):
        config(uri=uri)


def test_schema_object_names_are_unique_and_statements_are_idempotent():
    assert len(NEO4J_SCHEMA_OBJECT_NAMES) == len(NEO4J_SCHEMA_STATEMENTS)
    assert all("IF NOT EXISTS" in statement for statement in NEO4J_SCHEMA_STATEMENTS)
    for name in NEO4J_SCHEMA_OBJECT_NAMES:
        assert sum(name in statement for statement in NEO4J_SCHEMA_STATEMENTS) == 1


def test_bootstrap_verifies_connectivity_and_consumes_every_statement():
    driver, session = driver_with_session()
    adapter = Neo4jSchemaAdapter(config(), driver)

    applied = adapter.bootstrap_schema()

    assert applied == len(NEO4J_SCHEMA_STATEMENTS)
    driver.verify_connectivity.assert_called_once_with()
    driver.session.assert_called_once_with(database="neo4j")
    assert session.run.call_count == len(NEO4J_SCHEMA_STATEMENTS)
    for call, statement in zip(session.run.call_args_list, NEO4J_SCHEMA_STATEMENTS):
        assert call.args == (statement.strip(),)
        call.return_value.consume.assert_called_once_with()


def test_bootstrap_can_be_called_repeatedly():
    driver, session = driver_with_session()
    adapter = Neo4jSchemaAdapter(config(), driver)

    adapter.bootstrap_schema()
    adapter.bootstrap_schema()

    assert driver.verify_connectivity.call_count == 2
    assert driver.session.call_count == 2
    assert session.run.call_count == len(NEO4J_SCHEMA_STATEMENTS) * 2


def test_bootstrap_applies_transaction_timeout_to_every_schema_query():
    driver, _ = driver_with_session()
    timeouts = []
    adapter = Neo4jSchemaAdapter(
        config(query_timeout_seconds=1.5),
        driver,
        query_factory=lambda query, timeout: timeouts.append(timeout) or query,
    )

    adapter.bootstrap_schema()

    assert timeouts == [1.5] * len(NEO4J_SCHEMA_STATEMENTS)


def test_connect_applies_connection_timeout_and_query_factory():
    driver = MagicMock()
    graph_database = MagicMock()
    graph_database.driver.return_value = driver
    query = MagicMock(side_effect=lambda text, timeout: (text, timeout))
    neo4j_module = SimpleNamespace(GraphDatabase=graph_database, Query=query)

    with patch.dict("sys.modules", {"neo4j": neo4j_module}):
        adapter = Neo4jSchemaAdapter.connect(config(connection_timeout_seconds=4.0))

    graph_database.driver.assert_called_once_with(
        "neo4j://localhost:7687",
        auth=("neo4j", "secret-password"),
        connection_timeout=4.0,
    )
    assert adapter._query_factory("RETURN 1", 0.5) == ("RETURN 1", 0.5)


def test_adapter_context_closes_driver_once_and_rejects_later_bootstrap():
    driver, _ = driver_with_session()

    with Neo4jSchemaAdapter(config(), driver) as adapter:
        adapter.bootstrap_schema()

    adapter.close()
    driver.close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed"):
        adapter.bootstrap_schema()
