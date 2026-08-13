"""Composition root for the opt-in OSS relationship graph preview."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import uuid4

from mem0.configs.relationship_graph import RelationshipGraphConfig
from mem0.graphs.extractors import ExtractorIdentity, LLMStructuredRelationshipBackend, ValidatedRelationshipExtractor
from mem0.graphs.hooks import RelationshipGraphWriteHook
from mem0.graphs.neo4j import Neo4jGraphConfig, Neo4jSchemaAdapter
from mem0.graphs.operations import ProjectionOutboxReconciler, ProjectionWorkerService
from mem0.graphs.outbox import (
    ProjectionEventIntent,
    ProjectionEventProducer,
    ProjectionOutboxWorker,
    SQLiteProjectionOutbox,
)
from mem0.graphs.retrieval import RelationshipGraphSearch


class RelationshipGraphMemory(Protocol):
    collection_name: str
    config: Any
    llm: Any
    vector_store: Any


class RelationshipGraphRuntime:
    """Own graph connection, outbox, worker, retrieval, and administrative controls."""

    def __init__(
        self,
        *,
        graph: Neo4jSchemaAdapter,
        outbox: SQLiteProjectionOutbox,
        producer: ProjectionEventProducer,
        worker_service: ProjectionWorkerService,
        reconciler: ProjectionOutboxReconciler,
        write_hook: RelationshipGraphWriteHook,
        search: RelationshipGraphSearch,
        collection_name: str,
        reset_on_memory_reset: bool,
    ):
        self.graph = graph
        self.outbox = outbox
        self.producer = producer
        self.worker_service = worker_service
        self.reconciler = reconciler
        self.write_hook = write_hook
        self.search = search
        self.collection_name = collection_name
        self.reset_on_memory_reset = reset_on_memory_reset
        self._closed = False

    @classmethod
    def compose(cls, memory: RelationshipGraphMemory, config: RelationshipGraphConfig) -> "RelationshipGraphRuntime":
        if not config.enabled or config.uri is None or config.username is None or config.password is None:
            raise ValueError("relationship graph runtime requires enabled connection configuration")
        graph = Neo4jSchemaAdapter.connect(
            Neo4jGraphConfig(
                uri=config.uri,
                username=config.username,
                password=config.password,
                database=config.database,
                connection_timeout_seconds=config.connection_timeout_seconds,
                query_timeout_seconds=config.query_timeout_seconds,
            )
        )
        try:
            if config.bootstrap_schema:
                graph.bootstrap_schema()
            model_id = getattr(getattr(memory.config.llm, "config", None), "model", None)
            extractor = ValidatedRelationshipExtractor(
                identity=ExtractorIdentity(
                    name="mem0-relationship-extractor",
                    version="1",
                    model_id=model_id if isinstance(model_id, str) and model_id.strip() else None,
                ),
                backend=LLMStructuredRelationshipBackend(memory.llm),
            )
            outbox_path = config.outbox_path or _default_outbox_path(memory.config.history_db_path)
            outbox = SQLiteProjectionOutbox(outbox_path)
            producer = ProjectionEventProducer(outbox=outbox, extractor=extractor)
            worker = ProjectionOutboxWorker(
                outbox=outbox,
                projector=graph,
                worker_id=f"{os.getpid()}-{uuid4()}",
                lease_seconds=config.worker_lease_seconds,
                max_attempts=config.worker_max_attempts,
            )
            service = ProjectionWorkerService(worker=worker, outbox=outbox, poll_seconds=config.worker_poll_seconds)
            reconciler = ProjectionOutboxReconciler(
                outbox=outbox,
                producer=producer,
                read_memory_text=lambda intent: _read_memory_text(memory, intent),
            )
            runtime = cls(
                graph=graph,
                outbox=outbox,
                producer=producer,
                worker_service=service,
                reconciler=reconciler,
                write_hook=RelationshipGraphWriteHook(producer=producer),
                search=RelationshipGraphSearch(
                    adapter=graph,
                    graph_weight=config.graph_weight,
                    candidate_limit=config.candidate_limit,
                    explanation_limit=config.explanation_limit,
                    failure_threshold=config.circuit_breaker_failures,
                    cooldown_seconds=config.circuit_breaker_cooldown_seconds,
                ),
                collection_name=memory.collection_name,
                reset_on_memory_reset=config.reset_on_memory_reset,
            )
            if config.auto_start_worker:
                service.start()
            return runtime
        except Exception:
            graph.close()
            raise

    def reset(self) -> int:
        return self.graph.reset_collection(self.collection_name)

    def close(self) -> None:
        if self._closed:
            return
        self.worker_service.stop()
        self.outbox.close()
        self.graph.close()
        self._closed = True


def _default_outbox_path(history_db_path: str) -> Path:
    history = Path(history_db_path).expanduser()
    return history.with_name(f"{history.stem}-graph-outbox.sqlite3")


def _read_memory_text(memory: RelationshipGraphMemory, intent: ProjectionEventIntent) -> Optional[str]:
    record = memory.vector_store.get(intent.memory_id)
    if record is None:
        return None
    payload = record.get("payload") if isinstance(record, dict) else getattr(record, "payload", None)
    if not isinstance(payload, dict):
        return None
    for key, expected in intent.scope.canonical_values().items():
        if expected is not None and payload.get(key) != expected:
            return None
    text = payload.get("data")
    return text if isinstance(text, str) and text.strip() else None
