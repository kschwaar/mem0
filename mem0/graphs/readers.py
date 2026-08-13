"""Canonical-memory readers for explicit relationship-graph backfill."""

from __future__ import annotations

import base64
import json
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from typing import Any, Optional, Protocol

from pydantic import ValidationError

from mem0.graphs.backfill import CanonicalMemory, CanonicalMemoryPage
from mem0.graphs.models import GraphScope, SourceKind


class VectorStoreListProvider(Protocol):
    """Smallest vector-store surface required by the backfill reader."""

    def list(self, filters: Optional[dict[str, Any]] = None, top_k: Optional[int] = None) -> Any: ...


class CanonicalMemoryReadError(RuntimeError):
    """Base error for unsafe or malformed canonical-memory reads."""


class CanonicalMemoryRecordError(CanonicalMemoryReadError):
    """Raised when a vector-store row is not a usable canonical memory."""


class CanonicalMemorySnapshotLimitError(CanonicalMemoryReadError):
    """Raised instead of silently truncating a bounded vector-store snapshot."""


class VectorStoreCanonicalMemoryReader:
    """Page one bounded, exact-scope vector-store snapshot by stable memory ID."""

    def __init__(
        self,
        *,
        vector_store: VectorStoreListProvider,
        collection_name: str,
        max_records: int = 10_000,
    ):
        normalized_collection = collection_name.strip()
        if not normalized_collection:
            raise ValueError("collection_name must not be empty")
        if max_records < 1:
            raise ValueError("max_records must be at least 1")

        provider_collection = getattr(vector_store, "collection_name", None)
        if isinstance(provider_collection, str) and provider_collection != normalized_collection:
            raise ValueError("collection_name does not match the configured vector store")

        self._vector_store = vector_store
        self.collection_name = normalized_collection
        self.max_records = max_records

    def page(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        cursor: Optional[str],
        limit: int,
    ) -> CanonicalMemoryPage:
        normalized_collection = collection_name.strip()
        if normalized_collection != self.collection_name:
            raise ValueError("collection_name does not match this canonical-memory reader")
        if limit < 1:
            raise ValueError("limit must be at least 1")

        last_memory_id = _decode_cursor(cursor) if cursor is not None else None
        filters = {key: value for key, value in scope.canonical_values().items() if value is not None}
        raw_result = self._vector_store.list(filters=filters, top_k=self.max_records + 1)
        raw_rows = _unwrap_vector_store_rows(raw_result)
        if len(raw_rows) > self.max_records:
            raise CanonicalMemorySnapshotLimitError(
                f"canonical-memory snapshot exceeds the configured max_records limit of {self.max_records}"
            )

        memories = sorted((_canonical_memory(row, scope) for row in raw_rows), key=lambda memory: memory.memory_id)
        memory_ids = [memory.memory_id for memory in memories]
        if len(memory_ids) != len(set(memory_ids)):
            raise CanonicalMemoryRecordError("canonical-memory snapshot contains duplicate memory IDs")

        start = bisect_right(memory_ids, last_memory_id) if last_memory_id is not None else 0
        page_memories = memories[start : start + limit]
        has_more = start + len(page_memories) < len(memories)
        next_cursor = _encode_cursor(page_memories[-1].memory_id) if page_memories and has_more else None
        return CanonicalMemoryPage(memories=tuple(page_memories), next_cursor=next_cursor)


def _unwrap_vector_store_rows(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, tuple) and result and isinstance(result[0], (list, tuple)):
        return list(result[0])
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], (list, tuple)):
        return list(result[0])
    if isinstance(result, (str, bytes, Mapping)) or not isinstance(result, Iterable):
        raise CanonicalMemoryReadError("vector store returned an unsupported list result")
    return list(result)


def _canonical_memory(row: Any, scope: GraphScope) -> CanonicalMemory:
    memory_id = _row_value(row, "id")
    payload = _row_value(row, "payload")
    if not isinstance(payload, Mapping):
        raise CanonicalMemoryRecordError("canonical-memory row payload must be a mapping")

    normalized_memory_id = str(memory_id).strip() if memory_id is not None else ""
    if not normalized_memory_id:
        raise CanonicalMemoryRecordError("canonical-memory row is missing an ID")

    for scope_key, expected_value in scope.canonical_values().items():
        if expected_value is not None and payload.get(scope_key) != expected_value:
            raise CanonicalMemoryRecordError(
                f"canonical-memory row {normalized_memory_id!r} does not match the requested scope"
            )

    memory_text = payload.get("data")
    memory_hash = payload.get("hash")
    if not isinstance(memory_text, str) or not memory_text.strip():
        raise CanonicalMemoryRecordError(f"canonical-memory row {normalized_memory_id!r} has no memory text")
    if not isinstance(memory_hash, str) or not memory_hash.strip():
        raise CanonicalMemoryRecordError(f"canonical-memory row {normalized_memory_id!r} has no memory hash")

    try:
        return CanonicalMemory(
            memory_id=normalized_memory_id,
            memory_text=memory_text,
            memory_hash=memory_hash,
            source_kind=_source_kind(payload, normalized_memory_id),
        )
    except ValidationError as error:
        raise CanonicalMemoryRecordError(
            f"canonical-memory row {normalized_memory_id!r} violates the canonical record contract"
        ) from error


def _row_value(row: Any, field_name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field_name)
    return getattr(row, field_name, None)


def _source_kind(payload: Mapping[str, Any], memory_id: str) -> SourceKind:
    explicit_source = payload.get("source_kind")
    if explicit_source is not None:
        try:
            return SourceKind(str(explicit_source).strip().upper())
        except ValueError as error:
            raise CanonicalMemoryRecordError(
                f"canonical-memory row {memory_id!r} has an unsupported source_kind"
            ) from error

    role = payload.get("role")
    if isinstance(role, str):
        normalized_role = role.strip().upper()
        if normalized_role in {SourceKind.USER.value, SourceKind.ASSISTANT.value, SourceKind.SYSTEM.value}:
            return SourceKind(normalized_role)
    return SourceKind.IMPORTED


def _encode_cursor(memory_id: str) -> str:
    cursor_payload = json.dumps(
        {"last_memory_id": memory_id, "version": 1}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(cursor_payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("cursor is not a valid canonical-memory cursor") from error

    if (
        not isinstance(payload, dict)
        or set(payload) != {"last_memory_id", "version"}
        or payload.get("version") != 1
        or not isinstance(payload.get("last_memory_id"), str)
        or not payload["last_memory_id"]
    ):
        raise ValueError("cursor is not a valid canonical-memory cursor")
    return payload["last_memory_id"]
