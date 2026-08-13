"""Managed processing and privacy-safe operations for graph projection."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from mem0.graphs.outbox import (
    ProjectionEventIntent,
    ProjectionEventOperation,
    ProjectionEventProducer,
    ProjectionEventStatus,
    ProjectionOutboxStats,
    ProjectionOutboxWorker,
    SQLiteProjectionOutbox,
)


class ProjectionWorkerHealth(BaseModel):
    running: bool
    last_error_type: Optional[str] = None
    outbox: ProjectionOutboxStats
    lag_seconds: Optional[float] = Field(default=None, ge=0.0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectionReconciliationReport(BaseModel):
    examined: int = Field(ge=0)
    published: int = Field(ge=0)
    missing: int = Field(ge=0)
    hash_mismatch: int = Field(ge=0)
    failed: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectionWorkerService:
    """Run the single-event worker in a stoppable daemon thread or bounded drain."""

    def __init__(
        self,
        *,
        worker: ProjectionOutboxWorker,
        outbox: SQLiteProjectionOutbox,
        poll_seconds: float = 0.25,
    ):
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._worker = worker
        self._outbox = outbox
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error_type: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mem0-graph-projector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def drain(self, *, max_events: int = 1000) -> int:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        processed = 0
        while processed < max_events:
            event = self._worker.run_once()
            if event is None:
                break
            processed += 1
        return processed

    def health(self, *, now: Optional[datetime] = None) -> ProjectionWorkerHealth:
        stats = self._outbox.stats()
        lag = None
        if stats.oldest_unapplied_at is not None:
            current = now or datetime.now(timezone.utc)
            lag = max(0.0, (current - stats.oldest_unapplied_at).total_seconds())
        return ProjectionWorkerHealth(
            running=self.running,
            last_error_type=self._last_error_type,
            outbox=stats,
            lag_seconds=lag,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._worker.run_once()
                self._last_error_type = None
            except Exception as error:
                self._last_error_type = type(error).__name__
                event = None
            if event is None:
                self._stop.wait(self._poll_seconds)


class ProjectionOutboxReconciler:
    """Publish stranded PENDING intents from the canonical memory record."""

    def __init__(
        self,
        *,
        outbox: SQLiteProjectionOutbox,
        producer: ProjectionEventProducer,
        read_memory_text: Callable[[ProjectionEventIntent], Optional[str]],
    ):
        self._outbox = outbox
        self._producer = producer
        self._read_memory_text = read_memory_text

    def reconcile(self, *, limit: int = 100) -> ProjectionReconciliationReport:
        events = self._outbox.list_events(status=ProjectionEventStatus.PENDING, limit=limit)
        published = missing = hash_mismatch = failed = 0
        for event in events:
            intent = event.intent
            try:
                if intent.operation is ProjectionEventOperation.DELETE:
                    self._producer.publish(intent.event_id)
                    published += 1
                    continue
                text = self._read_memory_text(intent)
                if text is None:
                    missing += 1
                    continue
                if hashlib.md5(text.encode()).hexdigest() != intent.memory_hash:
                    hash_mismatch += 1
                    continue
                self._producer.publish(intent.event_id, memory_text=text)
                published += 1
            except Exception:
                failed += 1
        return ProjectionReconciliationReport(
            examined=len(events),
            published=published,
            missing=missing,
            hash_mismatch=hash_mismatch,
            failed=failed,
        )
