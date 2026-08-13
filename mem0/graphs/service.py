"""Manual orchestration for the relationship-graph vertical slice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mem0.graphs.extractors import ExtractorIdentity, RelationshipExtractor
from mem0.graphs.models import (
    GraphScope,
    ProjectionMethod,
    ProjectionResult,
    ProjectionSource,
    RelationshipCandidate,
    RelationshipProvenance,
    SourceKind,
    excerpt_sha256,
)


class MemoryGraphProjectionRequest(BaseModel):
    """Existing canonical memory supplied to the manual projection service."""

    memory_text: str = Field(min_length=1)
    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    memory_id: str = Field(min_length=1, max_length=512)
    memory_hash: str = Field(min_length=1, max_length=128)
    source_kind: SourceKind
    projection_method: ProjectionMethod = ProjectionMethod.MANUAL
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("memory_text")
    @classmethod
    def require_non_empty_memory_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory_text must not be empty")
        return value

    @field_validator("collection_name", "memory_id", "memory_hash")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("projection identifiers must not be empty")
        return normalized

    @field_validator("recorded_at")
    @classmethod
    def require_recorded_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value


class MemoryGraphProjectionResult(BaseModel):
    """Projection identifiers and verified provenance returned by one run."""

    projections: tuple[ProjectionResult, ...]
    provenance: tuple[RelationshipProvenance, ...]

    model_config = ConfigDict(frozen=True)


class RelationshipGraphProjectionAdapter(Protocol):
    def project_relationships(
        self,
        relationships: list[RelationshipCandidate],
        source: ProjectionSource,
    ) -> list[ProjectionResult]: ...

    def provenance_by_memory(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
    ) -> list[RelationshipProvenance]: ...


class ProjectionVerificationError(RuntimeError):
    """Raised when projected evidence cannot be read back from its exact scope."""


class MemoryGraphProjectionService:
    """Run manual extraction, atomic projection and exact-scope verification."""

    def __init__(
        self,
        *,
        extractor: RelationshipExtractor,
        adapter: RelationshipGraphProjectionAdapter,
    ):
        self._extractor = extractor
        self._adapter = adapter

    @property
    def extractor_identity(self) -> ExtractorIdentity:
        """Expose the stable identity needed to bind resumable backfill runs."""
        return self._extractor.identity

    def project(self, request: MemoryGraphProjectionRequest) -> MemoryGraphProjectionResult:
        candidates = self._extractor.extract(request.memory_text)
        if not candidates:
            return MemoryGraphProjectionResult(projections=(), provenance=())

        identity = self._extractor.identity
        source = ProjectionSource(
            collection_name=request.collection_name,
            scope=request.scope,
            memory_id=request.memory_id,
            memory_hash=request.memory_hash,
            excerpt_hash=excerpt_sha256(request.memory_text),
            source_kind=request.source_kind,
            projection_method=request.projection_method,
            extractor_name=identity.name,
            extractor_version=identity.version,
            model_id=identity.model_id,
            recorded_at=request.recorded_at,
        )
        projections = self._adapter.project_relationships(candidates, source)
        provenance = self._adapter.provenance_by_memory(
            collection_name=request.collection_name,
            scope=request.scope,
            memory_id=request.memory_id,
        )

        projected_evidence = {projection.evidence_id for projection in projections}
        visible_evidence = {record.evidence_id for record in provenance}
        missing_evidence = projected_evidence - visible_evidence
        if missing_evidence:
            missing = ", ".join(sorted(str(identifier) for identifier in missing_evidence))
            raise ProjectionVerificationError(
                f"projected evidence was not visible through exact-scope provenance readback: {missing}"
            )

        return MemoryGraphProjectionResult(projections=tuple(projections), provenance=tuple(provenance))
