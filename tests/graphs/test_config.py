import pytest
from pydantic import ValidationError

from mem0.configs.base import MemoryConfig
from mem0.configs.relationship_graph import RelationshipGraphConfig


def test_relationship_graph_is_default_off():
    config = MemoryConfig()

    assert config.relationship_graph.enabled is False
    assert config.relationship_graph.auto_start_worker is True
    assert "relationship_graph" in config.model_dump()


def test_enabled_graph_requires_connection_settings():
    with pytest.raises(ValidationError, match="requires uri, username, and password"):
        RelationshipGraphConfig(enabled=True)


def test_enabled_graph_reads_connection_from_environment(monkeypatch):
    monkeypatch.setenv("MEM0_GRAPH_NEO4J_URI", "neo4j://localhost:7687")
    monkeypatch.setenv("MEM0_GRAPH_NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("MEM0_GRAPH_NEO4J_PASSWORD", "private")

    config = RelationshipGraphConfig(enabled=True)

    assert config.uri == "neo4j://localhost:7687"
    assert config.username == "neo4j"
    assert config.password is not None
    assert config.password.get_secret_value() == "private"
    assert "private" not in repr(config)
    assert "private" not in config.model_dump_json()


def test_disabled_graph_does_not_require_or_read_connection_environment(monkeypatch):
    monkeypatch.setenv("MEM0_GRAPH_NEO4J_PASSWORD", "private")

    config = RelationshipGraphConfig()

    assert config.password is None
