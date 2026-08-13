"""Explicit OSS relationship-graph backfill command."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from mem0_cli.branding import print_error, print_success

console = Console()
err_console = Console(stderr=True)


def _load_graph_runtime():
    try:
        __import__("neo4j")
        from mem0 import Memory
        from mem0.graphs import (
            ExtractorIdentity,
            GraphScope,
            LLMStructuredRelationshipBackend,
            Neo4jGraphConfig,
            Neo4jSchemaAdapter,
            RelationshipGraphBackfill,
            ValidatedRelationshipExtractor,
        )
    except ImportError as error:
        raise RuntimeError(
            'Graph backfill requires the optional dependencies; install "mem0-cli[graphs]".'
        ) from error
    return {
        "Memory": Memory,
        "ExtractorIdentity": ExtractorIdentity,
        "GraphScope": GraphScope,
        "LLMStructuredRelationshipBackend": LLMStructuredRelationshipBackend,
        "Neo4jGraphConfig": Neo4jGraphConfig,
        "Neo4jSchemaAdapter": Neo4jSchemaAdapter,
        "RelationshipGraphBackfill": RelationshipGraphBackfill,
        "ValidatedRelationshipExtractor": ValidatedRelationshipExtractor,
    }


def _read_memory_config(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("memory config must be a readable JSON file") from error
    if not isinstance(parsed, dict):
        raise ValueError("memory config JSON must contain an object")
    return parsed


def _checkpoint_data(checkpoint) -> dict[str, Any]:
    return checkpoint.model_dump(mode="json", exclude={"completed_memory_ids"})


def cmd_graph_backfill(
    *,
    memory_config: Path,
    checkpoint_directory: Path,
    run_id: str,
    neo4j_uri: str,
    neo4j_username: str,
    neo4j_password: str,
    neo4j_database: str,
    user_id: str | None,
    agent_id: str | None,
    app_id: str | None,
    scope_run_id: str | None,
    page_size: int,
    max_memories: int | None,
    max_records: int,
    max_relationships: int,
    bootstrap_schema: bool,
    output: str,
) -> None:
    """Construct and execute one explicit OSS relationship-graph backfill."""
    from mem0_cli.state import is_agent_mode

    agent_mode = is_agent_mode()
    if agent_mode:
        output = "json"
    graph = None
    try:
        if output not in {"text", "json"}:
            raise ValueError("output must be 'text' or 'json'")
        runtime = _load_graph_runtime()
        scope = runtime["GraphScope"](
            user_id=user_id,
            agent_id=agent_id,
            app_id=app_id,
            run_id=scope_run_id,
        )
        memory = runtime["Memory"].from_config(_read_memory_config(memory_config))
        graph_config = runtime["Neo4jGraphConfig"](
            uri=neo4j_uri,
            username=neo4j_username,
            password=neo4j_password,
            database=neo4j_database,
        )
        graph = runtime["Neo4jSchemaAdapter"].connect(graph_config)
        if bootstrap_schema:
            graph.bootstrap_schema()

        llm_config = getattr(getattr(memory.config, "llm", None), "config", None)
        model_id = getattr(llm_config, "model", None)
        if not isinstance(model_id, str) or not model_id.strip():
            model_id = None
        extractor = runtime["ValidatedRelationshipExtractor"](
            identity=runtime["ExtractorIdentity"](
                name="mem0-relationship-extractor",
                version="1",
                model_id=model_id,
            ),
            backend=runtime["LLMStructuredRelationshipBackend"](memory.llm),
            max_relationships=max_relationships,
        )
        backfill = runtime["RelationshipGraphBackfill"](
            memory=memory,
            graph=graph,
            extractor=extractor,
            checkpoint_directory=checkpoint_directory,
            max_records=max_records,
        )
        checkpoint = backfill.run(
            run_id=run_id,
            scope=scope,
            page_size=page_size,
            max_memories=max_memories,
        )
    except Exception as error:
        print_error(
            err_console,
            f"Graph backfill failed ({type(error).__name__}).",
            hint="Check the configuration and privacy-safe checkpoint, then resume with the same run ID.",
        )
        raise typer.Exit(1) from None
    finally:
        if graph is not None:
            with suppress(Exception):
                graph.close()

    data = _checkpoint_data(checkpoint)
    if agent_mode:
        from mem0_cli.output import format_agent_envelope

        format_agent_envelope(console, command="graph backfill", data=data)
    elif output == "json":
        console.print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print_success(console, f"Graph backfill {checkpoint.status.value.lower()}.")
        console.print(
            f"Processed: {checkpoint.processed}  Projected: {checkpoint.projected}  "
            f"Skipped: {checkpoint.skipped}  Empty: {checkpoint.empty}  Failed: {checkpoint.failed}"
        )
        console.print(f"Checkpoint: {checkpoint_directory / f'{run_id}.json'}")


def default_checkpoint_directory() -> Path:
    return Path(os.environ.get("MEM0_DIR", Path.home() / ".mem0")) / "graph-backfill"
