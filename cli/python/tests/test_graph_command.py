import json
from io import StringIO
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mem0.graphs.backfill import BackfillCheckpoint, BackfillStatus
from mem0.graphs.extractors import ExtractorIdentity
from mem0.graphs.models import GraphScope
from mem0.graphs.operations import ProjectionReconciliationReport, ProjectionWorkerHealth
from mem0.graphs.outbox import ProjectionOutboxStats
from rich.console import Console
from typer import Exit

from mem0_cli.commands import graph as graph_command


class FakeMemory:
    calls: ClassVar[list[dict[str, Any]]] = []
    instance = SimpleNamespace(
        collection_name="memories",
        vector_store=object(),
        llm=object(),
        config=SimpleNamespace(llm=SimpleNamespace(config=SimpleNamespace(model="test-model"))),
    )

    @classmethod
    def from_config(cls, config):
        cls.calls.append(config)
        return cls.instance


class FakeGraphConfig:
    def __init__(self, **kwargs):
        self.values = kwargs


class FakeGraph:
    def __init__(self):
        self.bootstrap_calls = 0
        self.closed = False

    def bootstrap_schema(self):
        self.bootstrap_calls += 1

    def close(self):
        self.closed = True


class FakeGraphAdapter:
    graph: ClassVar[FakeGraph] = FakeGraph()
    configs: ClassVar[list[FakeGraphConfig]] = []

    @classmethod
    def connect(cls, config):
        cls.configs.append(config)
        return cls.graph


class FakeBackend:
    def __init__(self, llm):
        self.llm = llm


class FakeExtractor:
    instances: ClassVar[list["FakeExtractor"]] = []

    def __init__(self, **kwargs):
        self.values = kwargs
        self.instances.append(self)


class FakeBackfill:
    instances: ClassVar[list["FakeBackfill"]] = []

    def __init__(self, **kwargs):
        self.values = kwargs
        self.run_calls = []
        self.instances.append(self)

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return BackfillCheckpoint(
            run_id=kwargs["run_id"],
            collection_name="memories",
            scope=kwargs["scope"],
            extractor_name="mem0-relationship-extractor",
            extractor_version="1",
            completed_memory_ids=("private-memory-id",),
            processed=1,
            projected=1,
            status=BackfillStatus.COMPLETE,
        )


def runtime():
    return {
        "Memory": FakeMemory,
        "ExtractorIdentity": ExtractorIdentity,
        "GraphScope": GraphScope,
        "LLMStructuredRelationshipBackend": FakeBackend,
        "Neo4jGraphConfig": FakeGraphConfig,
        "Neo4jSchemaAdapter": FakeGraphAdapter,
        "RelationshipGraphBackfill": FakeBackfill,
        "ValidatedRelationshipExtractor": FakeExtractor,
    }


def invoke(tmp_path, monkeypatch, **overrides):
    from mem0_cli.state import set_agent_mode

    set_agent_mode(False)
    memory_config = tmp_path / "memory.json"
    memory_config.write_text(json.dumps({"vector_store": {"provider": "qdrant"}}), encoding="utf-8")
    output = StringIO()
    errors = StringIO()
    monkeypatch.setattr(
        graph_command, "console", Console(file=output, force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(
        graph_command, "err_console", Console(file=errors, force_terminal=False, no_color=True)
    )
    monkeypatch.setattr(graph_command, "_load_graph_runtime", runtime)
    arguments = {
        "memory_config": memory_config,
        "checkpoint_directory": tmp_path / "checkpoints",
        "run_id": "graph-run",
        "neo4j_uri": "neo4j://localhost:7687",
        "neo4j_username": "neo4j",
        "neo4j_password": "super-secret-password",
        "neo4j_database": "neo4j",
        "user_id": "user-1",
        "agent_id": None,
        "app_id": None,
        "scope_run_id": None,
        "page_size": 20,
        "max_memories": 5,
        "max_records": 100,
        "max_relationships": 10,
        "bootstrap_schema": True,
        "output": "json",
    }
    arguments.update(overrides)
    try:
        graph_command.cmd_graph_backfill(**arguments)
    except Exit as error:
        return output.getvalue(), errors.getvalue(), arguments, error
    return output.getvalue(), errors.getvalue(), arguments, None


def test_command_constructs_explicit_runtime_and_outputs_safe_checkpoint(tmp_path, monkeypatch):
    FakeMemory.calls.clear()
    FakeGraphAdapter.configs.clear()
    FakeGraphAdapter.graph = FakeGraph()
    FakeExtractor.instances.clear()
    FakeBackfill.instances.clear()

    output, errors, arguments, error = invoke(tmp_path, monkeypatch)

    assert error is None
    assert errors == ""
    assert FakeMemory.calls == [{"vector_store": {"provider": "qdrant"}}]
    assert FakeGraphAdapter.graph.bootstrap_calls == 1
    assert FakeGraphAdapter.graph.closed is True
    assert FakeGraphAdapter.configs[0].values == {
        "uri": "neo4j://localhost:7687",
        "username": "neo4j",
        "password": "super-secret-password",
        "database": "neo4j",
    }
    assert FakeExtractor.instances[0].values["identity"].model_id == "test-model"
    assert FakeExtractor.instances[0].values["max_relationships"] == 10
    assert FakeBackfill.instances[0].values["memory"] is FakeMemory.instance
    assert FakeBackfill.instances[0].values["graph"] is FakeGraphAdapter.graph
    assert (
        FakeBackfill.instances[0].values["checkpoint_directory"]
        == arguments["checkpoint_directory"]
    )
    assert FakeBackfill.instances[0].run_calls == [
        {
            "run_id": "graph-run",
            "scope": GraphScope(user_id="user-1"),
            "page_size": 20,
            "max_memories": 5,
        }
    ]
    parsed = json.loads(output)
    assert parsed["status"] == "COMPLETE"
    assert "completed_memory_ids" not in parsed
    assert "private-memory-id" not in output
    assert "super-secret-password" not in output


def test_command_does_not_bootstrap_when_disabled(tmp_path, monkeypatch):
    FakeGraphAdapter.graph = FakeGraph()

    _, _, _, error = invoke(tmp_path, monkeypatch, bootstrap_schema=False, output="text")

    assert error is None
    assert FakeGraphAdapter.graph.bootstrap_calls == 0
    assert FakeGraphAdapter.graph.closed is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"user_id": None},
        {"output": "yaml"},
    ],
)
def test_command_fails_safely_without_leaking_credentials(tmp_path, monkeypatch, overrides):
    output, errors, _, error = invoke(tmp_path, monkeypatch, **overrides)

    assert isinstance(error, Exit)
    assert "super-secret-password" not in output + errors


def test_invalid_memory_config_is_rejected_without_loading_runtime(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain an object"):
        graph_command._read_memory_config(path)


class FakeAdminRuntime:
    collection_name = "memories"

    def __init__(self):
        self.worker_service = MagicMock()
        self.worker_service.health.return_value = ProjectionWorkerHealth(
            running=False,
            outbox=ProjectionOutboxStats(
                pending=1,
                ready=2,
                processing=0,
                retry=0,
                applied=3,
                dead_letter=0,
            ),
        )
        self.worker_service.drain.return_value = 2
        self.reconciler = MagicMock()
        self.reconciler.reconcile.return_value = ProjectionReconciliationReport(
            examined=1,
            published=1,
            missing=0,
            hash_mismatch=0,
            failed=0,
        )
        self.outbox = MagicMock()
        self.outbox.replay.return_value = SimpleNamespace(
            intent=SimpleNamespace(event_id="event-1"), status=SimpleNamespace(value="READY")
        )
        self.reset = MagicMock(return_value=7)


class FakeAdminMemory:
    def __init__(self):
        self.relationship_graph = FakeAdminRuntime()
        self.close = MagicMock()


@pytest.mark.parametrize("action", ["status", "drain", "reconcile", "replay", "reset"])
def test_admin_actions_are_bounded_and_close_memory(tmp_path, monkeypatch, action):
    path = tmp_path / "memory.json"
    path.write_text("{}", encoding="utf-8")
    memory = FakeAdminMemory()
    output = StringIO()
    monkeypatch.setattr(graph_command, "_load_configured_memory", lambda config: memory)
    monkeypatch.setattr(
        graph_command, "console", Console(file=output, force_terminal=False, no_color=True)
    )

    graph_command.cmd_graph_admin(
        memory_config=path,
        action=action,
        output="json",
        event_id="event-1",
        limit=12,
        confirmed=True,
    )

    parsed = json.loads(output.getvalue())
    assert parsed
    memory.close.assert_called_once_with()
    if action == "drain":
        memory.relationship_graph.worker_service.drain.assert_called_once_with(max_events=12)
    if action == "reconcile":
        memory.relationship_graph.reconciler.reconcile.assert_called_once_with(limit=12)
    if action == "reset":
        memory.relationship_graph.reset.assert_called_once_with()


def test_reset_requires_explicit_confirmation(tmp_path, monkeypatch):
    path = tmp_path / "memory.json"
    path.write_text("{}", encoding="utf-8")
    memory = FakeAdminMemory()
    errors = StringIO()
    monkeypatch.setattr(graph_command, "_load_configured_memory", lambda config: memory)
    monkeypatch.setattr(
        graph_command, "err_console", Console(file=errors, force_terminal=False, no_color=True)
    )

    with pytest.raises(Exit):
        graph_command.cmd_graph_admin(memory_config=path, action="reset", output="json")

    memory.relationship_graph.reset.assert_not_called()
    memory.close.assert_called_once_with()
    assert "requires --yes" not in errors.getvalue()
