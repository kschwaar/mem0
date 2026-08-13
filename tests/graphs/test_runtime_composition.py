from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mem0.configs.base import MemoryConfig
from mem0.configs.relationship_graph import RelationshipGraphConfig
from mem0.memory.main import AsyncMemory, Memory


def configured(tmp_path):
    return MemoryConfig(
        history_db_path=str(tmp_path / "history.sqlite3"),
        relationship_graph=RelationshipGraphConfig(
            enabled=True,
            uri="neo4j://localhost:7687",
            username="neo4j",
            password="password",
            auto_start_worker=False,
            reset_on_memory_reset=True,
        ),
    )


def patch_factories(monkeypatch):
    vector_store = MagicMock()
    monkeypatch.setattr("mem0.memory.main.MEM0_TELEMETRY", False)
    monkeypatch.setattr("mem0.memory.main.EmbedderFactory.create", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("mem0.memory.main.VectorStoreFactory.create", MagicMock(return_value=vector_store))
    monkeypatch.setattr("mem0.memory.main.LlmFactory.create", MagicMock(return_value=MagicMock()))
    return vector_store


@pytest.mark.parametrize("memory_class", [Memory, AsyncMemory])
def test_enabled_config_composes_and_owns_graph_runtime(tmp_path, monkeypatch, memory_class):
    patch_factories(monkeypatch)
    runtime = SimpleNamespace(
        write_hook=object(),
        search=object(),
        reset_on_memory_reset=True,
        close=MagicMock(),
    )
    compose = MagicMock(return_value=runtime)
    monkeypatch.setattr("mem0.graphs.runtime.RelationshipGraphRuntime.compose", compose)

    memory = memory_class(configured(tmp_path))

    compose.assert_called_once_with(memory, memory.config.relationship_graph)
    assert memory.relationship_graph is runtime
    assert memory._graph_write_hook is runtime.write_hook
    assert memory._relationship_graph_search is runtime.search
    memory.close()
    runtime.close.assert_called_once_with()


def test_explicit_reset_policy_resets_graph_before_canonical_store(tmp_path, monkeypatch):
    vector_store = patch_factories(monkeypatch)
    order = []
    runtime = SimpleNamespace(
        write_hook=object(),
        search=object(),
        reset_on_memory_reset=True,
        reset=MagicMock(side_effect=lambda: order.append("graph")),
        close=MagicMock(),
    )
    monkeypatch.setattr("mem0.graphs.runtime.RelationshipGraphRuntime.compose", MagicMock(return_value=runtime))
    monkeypatch.setattr(
        "mem0.memory.main.VectorStoreFactory.reset",
        MagicMock(side_effect=lambda store: order.append("vector") or vector_store),
    )
    memory = Memory(configured(tmp_path))

    memory.reset()

    assert order[:2] == ["graph", "vector"]
    runtime.reset.assert_called_once_with()
    memory.close()


def test_disabled_config_never_composes_graph_runtime(tmp_path, monkeypatch):
    patch_factories(monkeypatch)
    compose = MagicMock()
    monkeypatch.setattr("mem0.graphs.runtime.RelationshipGraphRuntime.compose", compose)
    config = MemoryConfig(history_db_path=str(tmp_path / "history.sqlite3"))

    memory = Memory(config)

    compose.assert_not_called()
    assert memory.relationship_graph is None
    memory.close()
