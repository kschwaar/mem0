"""Pure domain models for the optional relationship graph.

This module deliberately has no Neo4j or Memory integration. It defines the
validated values and deterministic identities used by later projection code.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PREDICATE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SEMANTIC_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_NAMESPACE = uuid5(NAMESPACE_URL, "https://mem0.ai/relationship-graph/v1")


def _normalize_whitespace(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _canonical_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SourceKind(str, Enum):
    """Origin of the information represented by evidence."""

    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"
    IMPORTED = "IMPORTED"


class ProjectionMethod(str, Enum):
    """Mechanism that placed evidence into the graph."""

    LIVE = "LIVE"
    MANUAL = "MANUAL"
    BACKFILL = "BACKFILL"
    LEGACY_IMPORT = "LEGACY_IMPORT"


class AssertionState(str, Enum):
    """Lifecycle state of a projected semantic assertion."""

    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class GraphMemoryState(str, Enum):
    """Reconciliation state for one canonical memory's graph projection."""

    MISSING = "MISSING"
    CURRENT = "CURRENT"
    CONFLICT = "CONFLICT"
    INCOMPLETE = "INCOMPLETE"


class GraphScope(BaseModel):
    """Complete canonical scope tuple for a graph operation."""

    user_id: Optional[str] = Field(default=None, min_length=1, max_length=512)
    agent_id: Optional[str] = Field(default=None, min_length=1, max_length=512)
    app_id: Optional[str] = Field(default=None, min_length=1, max_length=512)
    run_id: Optional[str] = Field(default=None, min_length=1, max_length=512)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def require_identifier(self) -> "GraphScope":
        if not any((self.user_id, self.agent_id, self.app_id, self.run_id)):
            raise ValueError("graph scope requires at least one non-empty identifier")
        return self

    def canonical_values(self) -> dict[str, Optional[str]]:
        """Return every scope dimension in stable order, including nulls."""
        return {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "app_id": self.app_id,
            "run_id": self.run_id,
        }

    @property
    def key(self) -> str:
        canonical = json.dumps(self.canonical_values(), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EntityReference(BaseModel):
    """Normalized reference to an entity extracted from memory text."""

    text: str = Field(min_length=1, max_length=512)
    semantic_type: str = Field(default="UNSPECIFIED", min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = _normalize_whitespace(value)
        if not normalized:
            raise ValueError("entity text must not be empty")
        return normalized

    @field_validator("semantic_type")
    @classmethod
    def normalize_semantic_type(cls, value: str) -> str:
        normalized = _normalize_whitespace(value).replace("-", "_").replace(" ", "_").upper()
        if not _SEMANTIC_TYPE_PATTERN.fullmatch(normalized):
            raise ValueError("semantic_type must contain only uppercase letters, digits, and single underscores")
        return normalized

    @property
    def normalized_name(self) -> str:
        return self.text.casefold()

    def identity_values(self) -> dict[str, str]:
        return {"normalized_name": self.normalized_name, "semantic_type": self.semantic_type}


class RelationshipCandidate(BaseModel):
    """Validated relationship emitted by an extractor."""

    subject: EntityReference
    predicate: str = Field(min_length=1, max_length=64)
    object: EntityReference
    confidence: float = Field(ge=0.0, le=1.0)
    predicate_display: Optional[str] = Field(default=None, min_length=1, max_length=128)
    observed_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    allow_self_loop: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("predicate")
    @classmethod
    def normalize_predicate(cls, value: str) -> str:
        normalized = _normalize_whitespace(value).replace("-", "_").replace(" ", "_").casefold()
        if not _PREDICATE_PATTERN.fullmatch(normalized):
            raise ValueError("predicate must be lower-case snake case and start with a letter")
        return normalized

    @field_validator("predicate_display")
    @classmethod
    def normalize_predicate_display(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = _normalize_whitespace(value)
        if not normalized:
            raise ValueError("predicate_display must not be empty")
        return normalized

    @field_validator("confidence")
    @classmethod
    def require_finite_confidence(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("confidence must be finite")
        return value

    @field_validator("observed_at", "valid_from", "valid_to")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("relationship timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_relationship(self) -> "RelationshipCandidate":
        if not self.allow_self_loop and self.subject.identity_values() == self.object.identity_values():
            raise ValueError("self-loop relationships require allow_self_loop=True")
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class ProjectionSource(BaseModel):
    """Canonical memory and extraction provenance for one projection."""

    collection_name: str = Field(min_length=1, max_length=255)
    scope: GraphScope
    memory_id: str = Field(min_length=1, max_length=512)
    memory_hash: str = Field(min_length=1, max_length=128)
    excerpt_hash: str
    source_kind: SourceKind
    projection_method: ProjectionMethod = ProjectionMethod.MANUAL
    extractor_name: str = Field(min_length=1, max_length=128)
    extractor_version: str = Field(min_length=1, max_length=128)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=255)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("excerpt_hash")
    @classmethod
    def validate_excerpt_hash(cls, value: str) -> str:
        normalized = value.casefold()
        if not _SHA256_PATTERN.fullmatch(normalized):
            raise ValueError("excerpt_hash must be a 64-character SHA-256 hex digest")
        return normalized

    @field_validator("recorded_at")
    @classmethod
    def require_recorded_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value


class ProjectionResult(BaseModel):
    """Stable identifiers returned after projecting one relationship."""

    memory_id: str
    subject_entity_id: UUID
    object_entity_id: UUID
    assertion_id: UUID
    evidence_id: UUID

    model_config = ConfigDict(frozen=True)


class GraphLifecycleMutation(BaseModel):
    """Counts returned by one exact-scope lifecycle mutation."""

    memory_id: str
    evidence_deleted: int = Field(ge=0)
    assertions_retracted: int = Field(ge=0)

    model_config = ConfigDict(frozen=True)


class GraphUpdateMutation(GraphLifecycleMutation):
    """Atomic replacement result including new projection identities."""

    projections: tuple[ProjectionResult, ...]


class ProjectedEntity(BaseModel):
    """Entity fields returned with relationship provenance."""

    entity_id: UUID
    normalized_name: str
    display_name: str
    semantic_type: str

    model_config = ConfigDict(frozen=True)


class RelationshipProvenance(BaseModel):
    """One assertion and one supporting evidence record from an exact scope."""

    collection_name: str
    scope_key: str
    assertion_id: UUID
    subject: ProjectedEntity
    predicate: str
    predicate_display: str
    object: ProjectedEntity
    state: AssertionState
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    evidence_id: UUID
    memory_id: str
    memory_hash: str
    excerpt_hash: str
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: Optional[datetime] = None
    recorded_at: datetime
    source_kind: SourceKind
    projection_method: ProjectionMethod
    extractor_name: str
    extractor_version: str
    model_id: Optional[str] = None

    model_config = ConfigDict(frozen=True)


def excerpt_sha256(excerpt: str) -> str:
    """Hash the exact evidence excerpt without retaining it in graph identity data."""
    return hashlib.sha256(excerpt.encode("utf-8")).hexdigest()


def entity_id(collection_name: str, scope: GraphScope, entity: EntityReference) -> UUID:
    """Return the deterministic UUID for one exact scoped entity."""
    values = {
        "collection_name": collection_name,
        "scope_key": scope.key,
        **entity.identity_values(),
    }
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return uuid5(_IDENTITY_NAMESPACE, f"entity:{canonical}")


def assertion_dedupe_key(
    collection_name: str,
    scope: GraphScope,
    relationship: RelationshipCandidate,
) -> str:
    """Return the stable SHA-256 identity of one scoped semantic assertion."""
    values = {
        "collection_name": collection_name,
        "scope_key": scope.key,
        "subject": relationship.subject.identity_values(),
        "predicate": relationship.predicate,
        "object": relationship.object.identity_values(),
        "valid_from": _canonical_datetime(relationship.valid_from),
        "valid_to": _canonical_datetime(relationship.valid_to),
    }
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assertion_id(collection_name: str, scope: GraphScope, relationship: RelationshipCandidate) -> UUID:
    """Return the deterministic UUID for an assertion dedupe key."""
    return uuid5(_IDENTITY_NAMESPACE, f"assertion:{assertion_dedupe_key(collection_name, scope, relationship)}")


def evidence_id(assertion: UUID, source: ProjectionSource) -> UUID:
    """Return the deterministic UUID for evidence produced by one extraction version."""
    values = {
        "assertion_id": str(assertion),
        "memory_id": source.memory_id,
        "memory_hash": source.memory_hash,
        "excerpt_hash": source.excerpt_hash,
        "extractor_name": source.extractor_name,
        "extractor_version": source.extractor_version,
        "model_id": source.model_id,
    }
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return uuid5(_IDENTITY_NAMESPACE, f"evidence:{canonical}")
