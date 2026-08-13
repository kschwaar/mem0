"""Provider-neutral relationship extraction boundary.

Backends produce a structured payload. This module validates the complete
payload before returning any candidates, so callers can project only after the
entire extraction result is known to be valid.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mem0.graphs.models import RelationshipCandidate


class ExtractorIdentity(BaseModel):
    """Versioned identity recorded with evidence produced by an extractor."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RelationshipExtractionPayload(BaseModel):
    """Strict structured output accepted from a relationship backend."""

    relationships: list[RelationshipCandidate]

    model_config = ConfigDict(extra="forbid", frozen=True)


class RelationshipExtractionError(ValueError):
    """Raised when a backend fails or returns an invalid complete payload."""


@runtime_checkable
class StructuredRelationshipBackend(Protocol):
    """Minimal provider boundary; an LLM adapter may implement this later."""

    def extract(self, memory_text: str) -> Mapping[str, Any]: ...


@runtime_checkable
class RelationshipExtractor(Protocol):
    """Consumer-facing extractor that returns only validated candidates."""

    identity: ExtractorIdentity

    def extract(self, memory_text: str) -> list[RelationshipCandidate]: ...


class ValidatedRelationshipExtractor:
    """Validate a structured backend response atomically."""

    def __init__(
        self,
        *,
        identity: ExtractorIdentity,
        backend: StructuredRelationshipBackend,
        max_relationships: int = 50,
    ):
        if isinstance(max_relationships, bool) or not isinstance(max_relationships, int) or max_relationships < 1:
            raise ValueError("max_relationships must be a positive integer")
        self.identity = identity
        self._backend = backend
        self._max_relationships = max_relationships

    def extract(self, memory_text: str) -> list[RelationshipCandidate]:
        """Return a fully validated batch or raise without returning partial data."""
        if not isinstance(memory_text, str) or not memory_text.strip():
            raise ValueError("memory_text must be a non-empty string")

        try:
            raw_payload = self._backend.extract(memory_text)
        except Exception as error:
            raise RelationshipExtractionError(f"relationship backend {self.identity.name!r} failed") from error

        try:
            payload = RelationshipExtractionPayload.model_validate(raw_payload)
        except ValidationError as error:
            raise RelationshipExtractionError(
                f"relationship backend {self.identity.name!r} returned an invalid payload"
            ) from error

        if len(payload.relationships) > self._max_relationships:
            raise RelationshipExtractionError(
                f"relationship backend {self.identity.name!r} returned {len(payload.relationships)} relationships; "
                f"maximum is {self._max_relationships}"
            )

        return list(payload.relationships)
