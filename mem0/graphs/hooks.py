"""Opt-in bridge from canonical Memory writes to durable graph events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4

from mem0.graphs.models import GraphScope, SourceKind
from mem0.graphs.outbox import (
    ProjectionEvent,
    ProjectionEventIntent,
    ProjectionEventOperation,
    ProjectionEventProducer,
)


class RelationshipGraphWriteHook:
    """Prepare before a canonical mutation and publish after it succeeds."""

    def __init__(
        self,
        *,
        producer: ProjectionEventProducer,
        event_id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._producer = producer
        self._event_id_factory = event_id_factory
        self._clock = clock

    def prepare_add(
        self,
        *,
        collection_name: str,
        memory_id: str,
        memory_hash: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return self._prepare(
            operation=ProjectionEventOperation.UPSERT,
            collection_name=collection_name,
            memory_id=memory_id,
            memory_hash=memory_hash,
            metadata=metadata,
        )

    def prepare_update(
        self,
        *,
        collection_name: str,
        memory_id: str,
        previous_hash: str,
        memory_hash: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return self._prepare(
            operation=ProjectionEventOperation.UPDATE,
            collection_name=collection_name,
            memory_id=memory_id,
            previous_hash=previous_hash,
            memory_hash=memory_hash,
            metadata=metadata,
        )

    def prepare_delete(
        self,
        *,
        collection_name: str,
        memory_id: str,
        memory_hash: str,
        metadata: Mapping[str, Any],
    ) -> str:
        return self._prepare(
            operation=ProjectionEventOperation.DELETE,
            collection_name=collection_name,
            memory_id=memory_id,
            memory_hash=memory_hash,
            metadata=metadata,
        )

    def publish(self, event_id: str, *, memory_text: Optional[str] = None) -> ProjectionEvent:
        return self._producer.publish(event_id, memory_text=memory_text)

    def _prepare(
        self,
        *,
        operation: ProjectionEventOperation,
        collection_name: str,
        memory_id: str,
        memory_hash: str,
        metadata: Mapping[str, Any],
        previous_hash: Optional[str] = None,
    ) -> str:
        event_id = f"graph-{self._event_id_factory()}"
        intent = ProjectionEventIntent(
            event_id=event_id,
            operation=operation,
            collection_name=collection_name,
            scope=_scope_from_metadata(metadata),
            memory_id=memory_id,
            memory_hash=memory_hash,
            previous_hash=previous_hash,
            source_kind=_source_kind_from_metadata(metadata),
            occurred_at=self._clock(),
        )
        self._producer.prepare(intent)
        return event_id


def _scope_from_metadata(metadata: Mapping[str, Any]) -> GraphScope:
    return GraphScope(
        user_id=metadata.get("user_id"),
        agent_id=metadata.get("agent_id"),
        app_id=metadata.get("app_id"),
        run_id=metadata.get("run_id"),
    )


def _source_kind_from_metadata(metadata: Mapping[str, Any]) -> SourceKind:
    role = metadata.get("role")
    if isinstance(role, str):
        normalized = role.strip().casefold()
        if normalized == "assistant":
            return SourceKind.ASSISTANT
        if normalized == "system":
            return SourceKind.SYSTEM
    return SourceKind.USER
