"""Explicit update and delete lifecycle orchestration for relationship graphs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mem0.graphs.extractors import ExtractorIdentity, RelationshipExtractor
from mem0.graphs.models import (
    GraphLifecycleMutation,
    GraphScope,
    GraphUpdateMutation,
    ProjectionMethod,
    ProjectionSource,
    RelationshipCandidate,
    RelationshipProvenance,
    SourceKind,
    excerpt_sha256,
)
from mem0.graphs.service import ProjectionVerificationError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GraphMemoryUpdateRequest(BaseModel):
    """Canonical text update supplied after its vector-store write succeeds."""

    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    memory_id: str = Field(min_length=1, max_length=512)
    previous_hash: str = Field(min_length=1, max_length=128)
    memory_text: str = Field(min_length=1)
    memory_hash: str = Field(min_length=1, max_length=128)
    source_kind: SourceKind
    recorded_at: datetime = Field(default_factory=_now)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("collection_name", "memory_id", "previous_hash", "memory_hash")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("lifecycle identifiers must not be empty")
        return normalized

    @field_validator("memory_text")
    @classmethod
    def require_memory_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory_text must not be empty")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_changed_hash(self) -> "GraphMemoryUpdateRequest":
        if self.previous_hash == self.memory_hash:
            raise ValueError("memory_hash must differ from previous_hash")
        return self


class GraphMemoryDeleteRequest(BaseModel):
    """Canonical deletion tombstone identifying the exact graph version."""

    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    memory_id: str = Field(min_length=1, max_length=512)
    memory_hash: str = Field(min_length=1, max_length=128)
    deleted_at: datetime = Field(default_factory=_now)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("collection_name", "memory_id", "memory_hash")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("lifecycle identifiers must not be empty")
        return normalized

    @field_validator("deleted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deleted_at must include a timezone")
        return value


class GraphMemoryUpdateResult(BaseModel):
    mutation: GraphUpdateMutation
    provenance: tuple[RelationshipProvenance, ...]

    model_config = ConfigDict(frozen=True)


class RelationshipGraphLifecycleAdapter(Protocol):
    def replace_relationships(
        self,
        relationships: list[RelationshipCandidate],
        source: ProjectionSource,
        *,
        previous_hash: str,
    ) -> GraphUpdateMutation: ...

    def delete_memory(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
        memory_hash: str,
        deleted_at: datetime,
    ) -> GraphLifecycleMutation: ...

    def provenance_by_memory(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
    ) -> list[RelationshipProvenance]: ...


class RelationshipGraphLifecycleService:
    """Extract and atomically replace evidence, or apply a deletion tombstone."""

    def __init__(self, *, extractor: RelationshipExtractor, adapter: RelationshipGraphLifecycleAdapter):
        self._extractor = extractor
        self._adapter = adapter

    @property
    def extractor_identity(self) -> ExtractorIdentity:
        return self._extractor.identity

    def update(self, request: GraphMemoryUpdateRequest) -> GraphMemoryUpdateResult:
        relationships = self._extractor.extract(request.memory_text)
        identity = self._extractor.identity
        source = ProjectionSource(
            collection_name=request.collection_name,
            scope=request.scope,
            memory_id=request.memory_id,
            memory_hash=request.memory_hash,
            excerpt_hash=excerpt_sha256(request.memory_text),
            source_kind=request.source_kind,
            projection_method=ProjectionMethod.LIVE,
            extractor_name=identity.name,
            extractor_version=identity.version,
            model_id=identity.model_id,
            recorded_at=request.recorded_at,
        )
        mutation = self._adapter.replace_relationships(
            relationships,
            source,
            previous_hash=request.previous_hash,
        )
        provenance = self._adapter.provenance_by_memory(
            collection_name=request.collection_name,
            scope=request.scope,
            memory_id=request.memory_id,
        )
        projected_evidence = {projection.evidence_id for projection in mutation.projections}
        visible_evidence = {record.evidence_id for record in provenance}
        missing = projected_evidence - visible_evidence
        if missing:
            identifiers = ", ".join(sorted(str(identifier) for identifier in missing))
            raise ProjectionVerificationError(
                f"updated evidence was not visible through exact-scope provenance readback: {identifiers}"
            )
        if any(record.memory_hash != request.memory_hash for record in provenance):
            raise ProjectionVerificationError("stale evidence remained after graph memory update")
        return GraphMemoryUpdateResult(mutation=mutation, provenance=tuple(provenance))

    def delete(self, request: GraphMemoryDeleteRequest) -> GraphLifecycleMutation:
        return self._adapter.delete_memory(
            collection_name=request.collection_name,
            scope=request.scope,
            memory_id=request.memory_id,
            memory_hash=request.memory_hash,
            deleted_at=request.deleted_at,
        )
