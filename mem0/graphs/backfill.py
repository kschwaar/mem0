"""Sequential, resumable relationship-graph backfill orchestration."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mem0.graphs.extractors import RelationshipExtractionError
from mem0.graphs.models import GraphMemoryState, GraphScope, ProjectionMethod, SourceKind
from mem0.graphs.service import MemoryGraphProjectionRequest, MemoryGraphProjectionService, ProjectionVerificationError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CanonicalMemory(BaseModel):
    """Minimal canonical memory record required for relationship backfill."""

    memory_id: str = Field(min_length=1, max_length=512)
    memory_text: str = Field(min_length=1)
    memory_hash: str = Field(min_length=1, max_length=128)
    source_kind: SourceKind = SourceKind.IMPORTED

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("memory_text")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory_text must not be empty")
        return value

    @field_validator("memory_id", "memory_hash")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("canonical memory identifiers must not be empty")
        return normalized


class CanonicalMemoryPage(BaseModel):
    """One stable page of canonical memories and its continuation cursor."""

    memories: tuple[CanonicalMemory, ...]
    next_cursor: Optional[str] = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class BackfillStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    LIMIT_REACHED = "LIMIT_REACHED"


class BackfillFailureStage(str, Enum):
    READ = "READ"
    EXTRACT = "EXTRACT"
    PROJECT = "PROJECT"
    VERIFY = "VERIFY"
    RECONCILE = "RECONCILE"


class BackfillFailure(BaseModel):
    memory_id: Optional[str] = None
    stage: BackfillFailureStage
    error_type: str
    message: str
    attempt: int = Field(default=1, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class BackfillCheckpoint(BaseModel):
    """Privacy-safe progress record persisted after every handled memory."""

    run_id: str = Field(min_length=1, max_length=255)
    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    extractor_name: str = Field(min_length=1, max_length=128)
    extractor_version: str = Field(min_length=1, max_length=128)
    cursor: Optional[str] = None
    completed_memory_ids: tuple[str, ...] = ()
    processed: int = Field(default=0, ge=0)
    projected: int = Field(default=0, ge=0)
    empty: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    conflicts: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    failures: tuple[BackfillFailure, ...] = ()
    status: BackfillStatus = BackfillStatus.RUNNING
    started_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalMemoryReader(Protocol):
    def page(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        cursor: Optional[str],
        limit: int,
    ) -> CanonicalMemoryPage: ...


class GraphMemoryInspector(Protocol):
    def inspect(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
        memory_hash: str,
    ) -> GraphMemoryState: ...


class BackfillCheckpointStore(Protocol):
    def load(self, run_id: str) -> Optional[BackfillCheckpoint]: ...

    def save(self, checkpoint: BackfillCheckpoint) -> None: ...


class JsonBackfillCheckpointStore:
    """Persist one checkpoint per run using atomic file replacement."""

    def __init__(self, directory: Path):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        if not run_id or not all(character.isalnum() or character in "-_" for character in run_id):
            raise ValueError("run_id may contain only letters, digits, hyphens, and underscores")
        return run_id

    def path_for(self, run_id: str) -> Path:
        return self._directory / f"{self._validate_run_id(run_id)}.json"

    def load(self, run_id: str) -> Optional[BackfillCheckpoint]:
        path = self.path_for(run_id)
        if not path.exists():
            return None
        return BackfillCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, checkpoint: BackfillCheckpoint) -> None:
        path = self.path_for(checkpoint.run_id)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{checkpoint.run_id}.", suffix=".tmp", dir=self._directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(checkpoint.model_dump(mode="json"), temporary_file, indent=2, sort_keys=True)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, path)
            directory_descriptor = os.open(self._directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


class BackfillRunRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=255)
    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    page_size: int = Field(default=50, ge=1, le=1000)
    max_memories: Optional[int] = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return JsonBackfillCheckpointStore._validate_run_id(value)


class RelationshipGraphBackfillRunner:
    """Process canonical memories sequentially with resumable checkpoints."""

    def __init__(
        self,
        *,
        reader: CanonicalMemoryReader,
        inspector: GraphMemoryInspector,
        projector: MemoryGraphProjectionService,
        checkpoints: BackfillCheckpointStore,
    ):
        self._reader = reader
        self._inspector = inspector
        self._projector = projector
        self._checkpoints = checkpoints

    def run(self, request: BackfillRunRequest) -> BackfillCheckpoint:
        checkpoint = self._load_or_create_checkpoint(request)
        if checkpoint.status is BackfillStatus.COMPLETE:
            return checkpoint
        checkpoint = checkpoint.model_copy(update={"status": BackfillStatus.RUNNING, "completed_at": None})
        self._checkpoints.save(checkpoint)

        handled_this_run = 0
        completed_ids = set(checkpoint.completed_memory_ids)
        while True:
            try:
                page = self._reader.page(
                    collection_name=request.collection_name,
                    scope=request.scope,
                    cursor=checkpoint.cursor,
                    limit=request.page_size,
                )
            except Exception as error:
                checkpoint = self._record_failure(checkpoint, None, BackfillFailureStage.READ, error)
                self._checkpoints.save(checkpoint)
                return checkpoint

            for memory in page.memories:
                if memory.memory_id in completed_ids:
                    continue
                if request.max_memories is not None and handled_this_run >= request.max_memories:
                    checkpoint = checkpoint.model_copy(
                        update={"status": BackfillStatus.LIMIT_REACHED, "updated_at": _now()}
                    )
                    self._checkpoints.save(checkpoint)
                    return checkpoint

                checkpoint = self._handle_memory(request, checkpoint, memory)
                completed_ids.add(memory.memory_id)
                handled_this_run += 1
                checkpoint = checkpoint.model_copy(
                    update={
                        "completed_memory_ids": tuple((*checkpoint.completed_memory_ids, memory.memory_id)),
                        "processed": checkpoint.processed + 1,
                        "updated_at": _now(),
                    }
                )
                self._checkpoints.save(checkpoint)

            checkpoint = checkpoint.model_copy(update={"cursor": page.next_cursor, "updated_at": _now()})
            if page.next_cursor is None:
                checkpoint = checkpoint.model_copy(
                    update={"status": BackfillStatus.COMPLETE, "completed_at": _now(), "updated_at": _now()}
                )
                self._checkpoints.save(checkpoint)
                return checkpoint
            self._checkpoints.save(checkpoint)

    def _load_or_create_checkpoint(self, request: BackfillRunRequest) -> BackfillCheckpoint:
        existing = self._checkpoints.load(request.run_id)
        identity = self._projector.extractor_identity
        if existing is None:
            return BackfillCheckpoint(
                run_id=request.run_id,
                collection_name=request.collection_name,
                scope=request.scope,
                extractor_name=identity.name,
                extractor_version=identity.version,
            )
        expected = (request.collection_name, request.scope, identity.name, identity.version)
        actual = (existing.collection_name, existing.scope, existing.extractor_name, existing.extractor_version)
        if actual != expected:
            raise ValueError("backfill resume request does not match the existing checkpoint contract")
        return existing

    def _handle_memory(
        self,
        request: BackfillRunRequest,
        checkpoint: BackfillCheckpoint,
        memory: CanonicalMemory,
    ) -> BackfillCheckpoint:
        try:
            state = self._inspector.inspect(
                collection_name=request.collection_name,
                scope=request.scope,
                memory_id=memory.memory_id,
                memory_hash=memory.memory_hash,
            )
        except Exception as error:
            return self._record_failure(checkpoint, memory.memory_id, BackfillFailureStage.RECONCILE, error)

        if state is GraphMemoryState.CURRENT:
            return checkpoint.model_copy(update={"skipped": checkpoint.skipped + 1})
        if state in (GraphMemoryState.CONFLICT, GraphMemoryState.INCOMPLETE):
            failure = RuntimeError(f"graph memory state is {state.value}")
            updated = self._record_failure(checkpoint, memory.memory_id, BackfillFailureStage.RECONCILE, failure)
            return updated.model_copy(update={"conflicts": updated.conflicts + 1})

        try:
            result = self._projector.project(
                MemoryGraphProjectionRequest(
                    memory_text=memory.memory_text,
                    collection_name=request.collection_name,
                    scope=request.scope,
                    memory_id=memory.memory_id,
                    memory_hash=memory.memory_hash,
                    source_kind=memory.source_kind,
                    projection_method=ProjectionMethod.BACKFILL,
                )
            )
        except RelationshipExtractionError as error:
            return self._record_failure(
                checkpoint, memory.memory_id, BackfillFailureStage.EXTRACT, error, redactions=(memory.memory_text,)
            )
        except ProjectionVerificationError as error:
            return self._record_failure(
                checkpoint, memory.memory_id, BackfillFailureStage.VERIFY, error, redactions=(memory.memory_text,)
            )
        except Exception as error:
            return self._record_failure(
                checkpoint, memory.memory_id, BackfillFailureStage.PROJECT, error, redactions=(memory.memory_text,)
            )

        if result.projections:
            return checkpoint.model_copy(update={"projected": checkpoint.projected + 1})
        return checkpoint.model_copy(update={"empty": checkpoint.empty + 1})

    @staticmethod
    def _record_failure(
        checkpoint: BackfillCheckpoint,
        memory_id: Optional[str],
        stage: BackfillFailureStage,
        error: Exception,
        *,
        redactions: tuple[str, ...] = (),
    ) -> BackfillCheckpoint:
        message = str(error)
        for value in redactions:
            if value:
                message = message.replace(value, "[REDACTED]")
        failure = BackfillFailure(
            memory_id=memory_id,
            stage=stage,
            error_type=type(error).__name__,
            message=message,
        )
        return checkpoint.model_copy(
            update={
                "failed": checkpoint.failed + 1,
                "failures": tuple((*checkpoint.failures, failure)),
                "updated_at": _now(),
            }
        )
