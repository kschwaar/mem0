"""Durable SQLite outbox for eventual relationship-graph projection."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mem0.graphs.extractors import RelationshipExtractor
from mem0.graphs.models import (
    GraphScope,
    RelationshipCandidate,
    SourceKind,
    excerpt_sha256,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ProjectionEventOperation(str, Enum):
    UPSERT = "UPSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class ProjectionEventStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    PROCESSING = "PROCESSING"
    RETRY = "RETRY"
    APPLIED = "APPLIED"
    DEAD_LETTER = "DEAD_LETTER"


class ProjectionEventIntent(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    operation: ProjectionEventOperation
    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    memory_id: str = Field(min_length=1, max_length=512)
    memory_hash: str = Field(min_length=1, max_length=128)
    previous_hash: Optional[str] = Field(default=None, min_length=1, max_length=128)
    source_kind: SourceKind
    occurred_at: datetime = Field(default_factory=_now)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_hash_transition(self) -> "ProjectionEventIntent":
        if self.operation is ProjectionEventOperation.UPDATE:
            if self.previous_hash is None or self.previous_hash == self.memory_hash:
                raise ValueError("UPDATE events require a distinct previous_hash")
        elif self.previous_hash is not None:
            raise ValueError("previous_hash is supported only for UPDATE events")
        return self


class ProjectionEventPayload(BaseModel):
    relationships: tuple[RelationshipCandidate, ...] = ()
    excerpt_hash: Optional[str] = None
    extractor_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    extractor_version: Optional[str] = Field(default=None, min_length=1, max_length=128)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("excerpt_hash")
    @classmethod
    def validate_excerpt_hash(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.casefold()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("excerpt_hash must be a 64-character SHA-256 hex digest")
        return normalized


class ProjectionEvent(BaseModel):
    intent: ProjectionEventIntent
    status: ProjectionEventStatus
    payload: Optional[ProjectionEventPayload] = None
    attempts: int = Field(default=0, ge=0)
    available_at: datetime
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    last_error_type: Optional[str] = None
    last_error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    applied_at: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectionEventProjector(Protocol):
    def apply(self, event: ProjectionEvent) -> object: ...


class ProjectionOutboxConflictError(RuntimeError):
    """Raised when an event ID is reused for a different immutable intent."""


class ProjectionOutboxLeaseError(RuntimeError):
    """Raised when a worker attempts to finish an event it does not own."""


class ProjectionEventApplication(BaseModel):
    """Result of applying an event to the graph-side idempotency ledger."""

    event_id: str
    operation: ProjectionEventOperation
    already_applied: bool

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectionOutboxStats(BaseModel):
    """Privacy-safe queue counts suitable for health checks and metrics."""

    pending: int = Field(ge=0)
    ready: int = Field(ge=0)
    processing: int = Field(ge=0)
    retry: int = Field(ge=0)
    applied: int = Field(ge=0)
    dead_letter: int = Field(ge=0)
    oldest_unapplied_at: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class SQLiteProjectionOutbox:
    """SQLite projection ledger with atomic claims and expiring leases."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS graph_projection_events (
                event_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                collection_name TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                memory_hash TEXT NOT NULL,
                previous_hash TEXT,
                source_kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                last_error_type TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                applied_at TEXT
            );
            CREATE INDEX IF NOT EXISTS graph_projection_events_ready
            ON graph_projection_events(status, available_at, created_at);
            """
        )

    def create_pending(self, intent: ProjectionEventIntent) -> ProjectionEvent:
        now = _now()
        values = self._intent_values(intent)
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO graph_projection_events (
                        event_id, operation, status, collection_name, scope_json, memory_id,
                        memory_hash, previous_hash, source_kind, occurred_at, attempts,
                        available_at, created_at, updated_at
                    ) VALUES (?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (*values, _iso(now), _iso(now), _iso(now)),
                )
            except sqlite3.IntegrityError:
                existing = self.get(intent.event_id)
                if existing is None or existing.intent != intent:
                    raise ProjectionOutboxConflictError("projection event ID already has a different intent") from None
                return existing
        return self.get(intent.event_id)  # type: ignore[return-value]

    def mark_ready(self, event_id: str, payload: ProjectionEventPayload) -> ProjectionEvent:
        event = self.get(event_id)
        if event is None:
            raise KeyError(event_id)
        self._validate_payload(event.intent.operation, payload)
        serialized = json.dumps(payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        now = _now()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE graph_projection_events
                SET status = 'READY', payload_json = ?, available_at = ?, updated_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL,
                    last_error_type = NULL, last_error_message = NULL
                WHERE event_id = ? AND status = 'PENDING'
                """,
                (serialized, _iso(now), _iso(now), event_id),
            )
        if cursor.rowcount != 1:
            refreshed = self.get(event_id)
            if refreshed is None or refreshed.payload != payload or refreshed.status is ProjectionEventStatus.PENDING:
                raise ProjectionOutboxConflictError("projection event cannot be marked ready from its current state")
            return refreshed
        return self.get(event_id)  # type: ignore[return-value]

    def enqueue_ready(self, intent: ProjectionEventIntent, payload: ProjectionEventPayload) -> ProjectionEvent:
        event = self.create_pending(intent)
        if event.status is ProjectionEventStatus.PENDING:
            return self.mark_ready(intent.event_id, payload)
        if event.payload != payload:
            raise ProjectionOutboxConflictError("projection event ID already has a different payload")
        return event

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: Optional[datetime] = None,
    ) -> Optional[ProjectionEvent]:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or _now()
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT event_id FROM graph_projection_events
                    WHERE (
                        status IN ('READY', 'RETRY') AND available_at <= ?
                    ) OR (
                        status = 'PROCESSING' AND lease_expires_at <= ?
                    )
                    ORDER BY created_at ASC, event_id ASC
                    LIMIT 1
                    """,
                    (_iso(claimed_at), _iso(claimed_at)),
                ).fetchone()
                if row is None:
                    self._connection.execute("COMMIT")
                    return None
                self._connection.execute(
                    """
                    UPDATE graph_projection_events
                    SET status = 'PROCESSING', attempts = attempts + 1,
                        lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (worker_id, _iso(expires_at), _iso(claimed_at), row["event_id"]),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self.get(row["event_id"])

    def mark_applied(self, event_id: str, *, worker_id: str, now: Optional[datetime] = None) -> ProjectionEvent:
        applied_at = now or _now()
        self._finish_owned(
            event_id,
            worker_id,
            """
            status = 'APPLIED', applied_at = ?, updated_at = ?,
            lease_owner = NULL, lease_expires_at = NULL,
            last_error_type = NULL, last_error_message = NULL
            """,
            (_iso(applied_at), _iso(applied_at)),
        )
        return self.get(event_id)  # type: ignore[return-value]

    def mark_failed(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: Exception,
        max_attempts: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
        now: Optional[datetime] = None,
    ) -> ProjectionEvent:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if base_delay_seconds < 0 or max_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        event = self.get(event_id)
        if event is None:
            raise KeyError(event_id)
        failed_at = now or _now()
        dead = event.attempts >= max_attempts
        delay = min(max_delay_seconds, base_delay_seconds * (2 ** max(0, event.attempts - 1)))
        available_at = failed_at if dead else failed_at + timedelta(seconds=delay)
        status = ProjectionEventStatus.DEAD_LETTER if dead else ProjectionEventStatus.RETRY
        self._finish_owned(
            event_id,
            worker_id,
            """
            status = ?, available_at = ?, updated_at = ?,
            lease_owner = NULL, lease_expires_at = NULL,
            last_error_type = ?, last_error_message = ?
            """,
            (
                status.value,
                _iso(available_at),
                _iso(failed_at),
                type(error).__name__,
                "projection attempt failed",
            ),
        )
        return self.get(event_id)  # type: ignore[return-value]

    def replay(self, event_id: str, *, now: Optional[datetime] = None) -> ProjectionEvent:
        replayed_at = now or _now()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE graph_projection_events
                SET status = 'READY', attempts = 0, available_at = ?, updated_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL,
                    last_error_type = NULL, last_error_message = NULL, applied_at = NULL
                WHERE event_id = ? AND status IN ('DEAD_LETTER', 'APPLIED')
                """,
                (_iso(replayed_at), _iso(replayed_at), event_id),
            )
        if cursor.rowcount != 1:
            raise ProjectionOutboxConflictError("only applied or dead-letter events can be replayed")
        return self.get(event_id)  # type: ignore[return-value]

    def get(self, event_id: str) -> Optional[ProjectionEvent]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM graph_projection_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return None if row is None else self._event_from_row(row)

    def list_events(
        self,
        *,
        status: Optional[ProjectionEventStatus] = None,
        limit: int = 100,
    ) -> list[ProjectionEvent]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = "SELECT * FROM graph_projection_events"
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status.value,)
        query += " ORDER BY created_at ASC, event_id ASC LIMIT ?"
        with self._lock:
            rows = self._connection.execute(query, (*parameters, limit)).fetchall()
        return [self._event_from_row(row) for row in rows]

    def stats(self) -> ProjectionOutboxStats:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, count(*) AS count FROM graph_projection_events GROUP BY status"
            ).fetchall()
            oldest = self._connection.execute(
                """
                SELECT min(created_at) AS oldest_unapplied_at
                FROM graph_projection_events
                WHERE status NOT IN ('APPLIED', 'DEAD_LETTER')
                """
            ).fetchone()
        counts = {status.value: 0 for status in ProjectionEventStatus}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return ProjectionOutboxStats(
            pending=counts[ProjectionEventStatus.PENDING.value],
            ready=counts[ProjectionEventStatus.READY.value],
            processing=counts[ProjectionEventStatus.PROCESSING.value],
            retry=counts[ProjectionEventStatus.RETRY.value],
            applied=counts[ProjectionEventStatus.APPLIED.value],
            dead_letter=counts[ProjectionEventStatus.DEAD_LETTER.value],
            oldest_unapplied_at=oldest["oldest_unapplied_at"],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _finish_owned(self, event_id: str, worker_id: str, assignments: str, values: tuple[object, ...]) -> None:
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE graph_projection_events SET {assignments} "
                "WHERE event_id = ? AND status = 'PROCESSING' AND lease_owner = ?",
                (*values, event_id, worker_id),
            )
        if cursor.rowcount != 1:
            raise ProjectionOutboxLeaseError("projection event lease is not owned by this worker")

    @staticmethod
    def _validate_payload(operation: ProjectionEventOperation, payload: ProjectionEventPayload) -> None:
        if operation is ProjectionEventOperation.DELETE:
            if payload != ProjectionEventPayload():
                raise ValueError("DELETE events must use an empty payload")
            return
        required = (payload.excerpt_hash, payload.extractor_name, payload.extractor_version)
        if not all(required):
            raise ValueError("UPSERT and UPDATE payloads require extraction provenance")

    @staticmethod
    def _intent_values(intent: ProjectionEventIntent) -> tuple[object, ...]:
        return (
            intent.event_id,
            intent.operation.value,
            intent.collection_name,
            json.dumps(intent.scope.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
            intent.memory_id,
            intent.memory_hash,
            intent.previous_hash,
            intent.source_kind.value,
            _iso(intent.occurred_at),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ProjectionEvent:
        payload = ProjectionEventPayload.model_validate_json(row["payload_json"]) if row["payload_json"] else None
        return ProjectionEvent(
            intent=ProjectionEventIntent(
                event_id=row["event_id"],
                operation=row["operation"],
                collection_name=row["collection_name"],
                scope=json.loads(row["scope_json"]),
                memory_id=row["memory_id"],
                memory_hash=row["memory_hash"],
                previous_hash=row["previous_hash"],
                source_kind=row["source_kind"],
                occurred_at=row["occurred_at"],
            ),
            status=row["status"],
            payload=payload,
            attempts=row["attempts"],
            available_at=row["available_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            last_error_type=row["last_error_type"],
            last_error_message=row["last_error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            applied_at=row["applied_at"],
        )


class ProjectionEventProducer:
    """Turn a live canonical-memory mutation into a durable projection event."""

    def __init__(self, *, outbox: SQLiteProjectionOutbox, extractor: RelationshipExtractor):
        self._outbox = outbox
        self._extractor = extractor

    def prepare(self, intent: ProjectionEventIntent) -> ProjectionEvent:
        """Durably record mutation intent before the canonical store changes."""
        return self._outbox.create_pending(intent)

    def publish(self, event_id: str, *, memory_text: Optional[str] = None) -> ProjectionEvent:
        """Extract and publish a prepared event after the canonical mutation commits."""
        pending = self._outbox.get(event_id)
        if pending is None:
            raise KeyError(event_id)
        if pending.status is not ProjectionEventStatus.PENDING:
            return pending

        intent = pending.intent
        if intent.operation is ProjectionEventOperation.DELETE:
            if memory_text is not None:
                raise ValueError("DELETE events do not accept memory text")
            return self._outbox.mark_ready(intent.event_id, ProjectionEventPayload())
        if memory_text is None:
            raise ValueError("UPSERT and UPDATE events require memory text")

        relationships = tuple(self._extractor.extract(memory_text))
        identity = self._extractor.identity
        payload = ProjectionEventPayload(
            relationships=relationships,
            excerpt_hash=excerpt_sha256(memory_text),
            extractor_name=identity.name,
            extractor_version=identity.version,
            model_id=identity.model_id,
        )
        return self._outbox.mark_ready(intent.event_id, payload)

    def enqueue(self, intent: ProjectionEventIntent, *, memory_text: Optional[str] = None) -> ProjectionEvent:
        """Persist intent before extraction, then publish only a fully validated payload."""
        if intent.operation is ProjectionEventOperation.DELETE and memory_text is not None:
            raise ValueError("DELETE events do not accept memory text")
        if intent.operation is not ProjectionEventOperation.DELETE and memory_text is None:
            raise ValueError("UPSERT and UPDATE events require memory text")
        self.prepare(intent)
        return self.publish(intent.event_id, memory_text=memory_text)


class ProjectionOutboxWorker:
    """Claim and apply at most one projection event per invocation."""

    def __init__(
        self,
        *,
        outbox: SQLiteProjectionOutbox,
        projector: ProjectionEventProjector,
        worker_id: str,
        lease_seconds: float = 30,
        max_attempts: int = 5,
        base_delay_seconds: float = 1,
        max_delay_seconds: float = 300,
        clock: Callable[[], datetime] = _now,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if base_delay_seconds < 0 or max_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        self._outbox = outbox
        self._projector = projector
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._clock = clock

    def run_once(self) -> Optional[ProjectionEvent]:
        now = self._clock()
        event = self._outbox.claim(worker_id=self._worker_id, lease_seconds=self._lease_seconds, now=now)
        if event is None:
            return None
        try:
            self._projector.apply(event)
        except Exception as error:
            return self._outbox.mark_failed(
                event.intent.event_id,
                worker_id=self._worker_id,
                error=error,
                max_attempts=self._max_attempts,
                base_delay_seconds=self._base_delay_seconds,
                max_delay_seconds=self._max_delay_seconds,
                now=self._clock(),
            )
        return self._outbox.mark_applied(event.intent.event_id, worker_id=self._worker_id, now=self._clock())
