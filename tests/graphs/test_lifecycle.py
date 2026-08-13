from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from mem0.graphs.extractors import ExtractorIdentity
from mem0.graphs.lifecycle import (
    GraphMemoryDeleteRequest,
    GraphMemoryUpdateRequest,
    RelationshipGraphLifecycleService,
)
from mem0.graphs.models import GraphLifecycleMutation, GraphUpdateMutation, ProjectionResult, SourceKind
from mem0.graphs.service import ProjectionVerificationError


EVIDENCE_ID = UUID("44444444-4444-5444-8444-444444444444")


class FakeExtractor:
    identity = ExtractorIdentity(name="lifecycle", version="1", model_id="test-model")

    def __init__(self, relationships=None, error=None):
        self.relationships = (
            relationships
            if relationships is not None
            else [
                {
                    "subject": {"text": "Alice", "semantic_type": "PERSON"},
                    "predicate": "works_at",
                    "object": {"text": "Beta", "semantic_type": "ORG"},
                    "confidence": 0.9,
                }
            ]
        )
        self.error = error
        self.calls = []

    def extract(self, memory_text):
        self.calls.append(memory_text)
        if self.error:
            raise self.error
        from mem0.graphs.models import RelationshipCandidate

        return [RelationshipCandidate.model_validate(item) for item in self.relationships]


class FakeAdapter:
    def __init__(self, provenance=None):
        self.provenance = provenance or []
        self.replace_calls = []
        self.delete_calls = []

    def replace_relationships(self, relationships, source, *, previous_hash):
        self.replace_calls.append((relationships, source, previous_hash))
        return GraphUpdateMutation(
            memory_id=source.memory_id,
            evidence_deleted=1,
            assertions_retracted=1,
            projections=(
                ProjectionResult(
                    memory_id=source.memory_id,
                    subject_entity_id="11111111-1111-5111-8111-111111111111",
                    object_entity_id="22222222-2222-5222-8222-222222222222",
                    assertion_id="33333333-3333-5333-8333-333333333333",
                    evidence_id=EVIDENCE_ID,
                ),
            )
            if relationships
            else (),
        )

    def provenance_by_memory(self, **kwargs):
        return list(self.provenance)

    def delete_memory(self, **kwargs):
        self.delete_calls.append(kwargs)
        return GraphLifecycleMutation(memory_id=kwargs["memory_id"], evidence_deleted=2, assertions_retracted=1)


def update_request(**overrides):
    values = {
        "collection_name": " memories ",
        "scope": {"user_id": "user-1"},
        "memory_id": " memory-1 ",
        "previous_hash": " old-hash ",
        "memory_text": "Alice now works at Beta",
        "memory_hash": " new-hash ",
        "source_kind": SourceKind.USER,
        "recorded_at": datetime(2026, 8, 13, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return GraphMemoryUpdateRequest(**values)


def test_update_extracts_before_atomic_replace_and_preserves_no_raw_text_in_adapter_call():
    extractor = FakeExtractor(relationships=[])
    adapter = FakeAdapter()
    service = RelationshipGraphLifecycleService(extractor=extractor, adapter=adapter)

    result = service.update(update_request())

    assert extractor.calls == ["Alice now works at Beta"]
    relationships, source, previous_hash = adapter.replace_calls[0]
    assert relationships == []
    assert previous_hash == "old-hash"
    assert source.memory_hash == "new-hash"
    assert source.projection_method.value == "LIVE"
    assert not hasattr(source, "memory_text")
    assert result.mutation.evidence_deleted == 1
    assert result.provenance == ()


def test_extraction_failure_does_not_mutate_graph():
    extractor = FakeExtractor(error=RuntimeError("invalid extraction"))
    adapter = FakeAdapter()
    service = RelationshipGraphLifecycleService(extractor=extractor, adapter=adapter)

    with pytest.raises(RuntimeError, match="invalid extraction"):
        service.update(update_request())

    assert adapter.replace_calls == []


def test_update_verification_rejects_missing_or_stale_evidence():
    adapter = FakeAdapter(provenance=[])
    service = RelationshipGraphLifecycleService(extractor=FakeExtractor(), adapter=adapter)
    with pytest.raises(ProjectionVerificationError, match=str(EVIDENCE_ID)):
        service.update(update_request())

    stale = type("Provenance", (), {"evidence_id": EVIDENCE_ID, "memory_hash": "old-hash"})()
    adapter = FakeAdapter(provenance=[stale])
    service = RelationshipGraphLifecycleService(extractor=FakeExtractor(), adapter=adapter)
    with pytest.raises(ProjectionVerificationError, match="stale evidence"):
        service.update(update_request())


def test_delete_forwards_only_exact_identity_and_tombstone_time():
    adapter = FakeAdapter()
    service = RelationshipGraphLifecycleService(extractor=FakeExtractor(), adapter=adapter)
    request = GraphMemoryDeleteRequest(
        collection_name=" memories ",
        scope={"user_id": "user-1"},
        memory_id=" memory-1 ",
        memory_hash=" old-hash ",
        deleted_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    result = service.delete(request)

    assert result.evidence_deleted == 2
    assert adapter.delete_calls == [
        {
            "collection_name": "memories",
            "scope": request.scope,
            "memory_id": "memory-1",
            "memory_hash": "old-hash",
            "deleted_at": request.deleted_at,
        }
    ]


def test_lifecycle_requests_reject_same_hash_empty_text_and_naive_timestamps():
    with pytest.raises(ValidationError, match="must differ"):
        update_request(memory_hash="old-hash")
    with pytest.raises(ValidationError, match="memory_text"):
        update_request(memory_text=" ")
    with pytest.raises(ValidationError, match="timezone"):
        update_request(recorded_at=datetime(2026, 8, 13))
    with pytest.raises(ValidationError, match="timezone"):
        GraphMemoryDeleteRequest(
            collection_name="memories",
            scope={"user_id": "user-1"},
            memory_id="memory-1",
            memory_hash="hash",
            deleted_at=datetime(2026, 8, 13),
        )
