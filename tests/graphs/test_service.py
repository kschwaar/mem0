from uuid import UUID

import pytest
from pydantic import ValidationError

from mem0.graphs.extractors import ExtractorIdentity, RelationshipExtractionError
from mem0.graphs.models import (
    AssertionState,
    ProjectionMethod,
    ProjectionResult,
    RelationshipCandidate,
    RelationshipProvenance,
    SourceKind,
    excerpt_sha256,
)
from mem0.graphs.service import (
    MemoryGraphProjectionRequest,
    MemoryGraphProjectionService,
    ProjectionVerificationError,
)


ASSERTION_ID = UUID("33333333-3333-5333-8333-333333333333")
EVIDENCE_ID = UUID("44444444-4444-5444-8444-444444444444")


def candidate():
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Acme", "semantic_type": "ORG"},
        confidence=0.91,
    )


def request(**overrides):
    values = {
        "memory_text": "Alice works at Acme",
        "collection_name": " memories ",
        "scope": {"user_id": "user-1"},
        "memory_id": " memory-1 ",
        "memory_hash": " hash-1 ",
        "source_kind": SourceKind.USER,
        "recorded_at": "2026-07-21T00:00:00Z",
    }
    values.update(overrides)
    return MemoryGraphProjectionRequest(**values)


def projection():
    return ProjectionResult(
        memory_id="memory-1",
        subject_entity_id="11111111-1111-5111-8111-111111111111",
        object_entity_id="22222222-2222-5222-8222-222222222222",
        assertion_id=ASSERTION_ID,
        evidence_id=EVIDENCE_ID,
    )


def provenance(**overrides):
    values = {
        "collection_name": "memories",
        "scope_key": request().scope.key,
        "assertion_id": ASSERTION_ID,
        "subject": {
            "entity_id": "11111111-1111-5111-8111-111111111111",
            "normalized_name": "alice",
            "display_name": "Alice",
            "semantic_type": "PERSON",
        },
        "predicate": "works_at",
        "predicate_display": "works at",
        "object": {
            "entity_id": "22222222-2222-5222-8222-222222222222",
            "normalized_name": "acme",
            "display_name": "Acme",
            "semantic_type": "ORG",
        },
        "state": AssertionState.ACTIVE,
        "evidence_id": EVIDENCE_ID,
        "memory_id": "memory-1",
        "memory_hash": "hash-1",
        "excerpt_hash": excerpt_sha256("Alice works at Acme"),
        "confidence": 0.91,
        "recorded_at": "2026-07-21T00:00:00Z",
        "source_kind": SourceKind.USER,
        "projection_method": ProjectionMethod.MANUAL,
        "extractor_name": "manual-structured",
        "extractor_version": "1",
        "model_id": "test-model",
    }
    values.update(overrides)
    return RelationshipProvenance(**values)


class FakeExtractor:
    identity = ExtractorIdentity(name="manual-structured", version="1", model_id="test-model")

    def __init__(self, candidates=None, error=None):
        self.candidates = [candidate()] if candidates is None else candidates
        self.error = error
        self.calls = []

    def extract(self, memory_text):
        self.calls.append(memory_text)
        if self.error:
            raise self.error
        return list(self.candidates)


class FakeAdapter:
    def __init__(self, projections=None, provenance_records=None):
        self.projections = [projection()] if projections is None else projections
        self.provenance_records = [provenance()] if provenance_records is None else provenance_records
        self.project_calls = []
        self.read_calls = []

    def project_relationships(self, relationships, source):
        self.project_calls.append((relationships, source))
        return list(self.projections)

    def provenance_by_memory(self, **kwargs):
        self.read_calls.append(kwargs)
        return list(self.provenance_records)


def test_manual_service_extracts_projects_and_verifies_readback():
    extractor = FakeExtractor()
    adapter = FakeAdapter()
    service = MemoryGraphProjectionService(extractor=extractor, adapter=adapter)
    projection_request = request()

    result = service.project(projection_request)

    assert extractor.calls == ["Alice works at Acme"]
    assert len(adapter.project_calls) == 1
    projected_candidates, source = adapter.project_calls[0]
    assert projected_candidates == [candidate()]
    assert source.collection_name == "memories"
    assert source.memory_id == "memory-1"
    assert source.memory_hash == "hash-1"
    assert source.excerpt_hash == excerpt_sha256(projection_request.memory_text)
    assert source.extractor_name == "manual-structured"
    assert source.extractor_version == "1"
    assert source.model_id == "test-model"
    assert adapter.read_calls == [
        {
            "collection_name": "memories",
            "scope": projection_request.scope,
            "memory_id": "memory-1",
        }
    ]
    assert result.projections == (projection(),)
    assert result.provenance == (provenance(),)


def test_empty_extraction_does_not_touch_graph_adapter():
    extractor = FakeExtractor(candidates=[])
    adapter = FakeAdapter()
    service = MemoryGraphProjectionService(extractor=extractor, adapter=adapter)

    result = service.project(request())

    assert result.projections == ()
    assert result.provenance == ()
    assert adapter.project_calls == []
    assert adapter.read_calls == []


def test_extraction_failure_does_not_touch_graph_adapter():
    extractor = FakeExtractor(error=RelationshipExtractionError("invalid complete payload"))
    adapter = FakeAdapter()
    service = MemoryGraphProjectionService(extractor=extractor, adapter=adapter)

    with pytest.raises(RelationshipExtractionError):
        service.project(request())

    assert adapter.project_calls == []
    assert adapter.read_calls == []


def test_missing_projected_evidence_fails_readback_verification():
    adapter = FakeAdapter(provenance_records=[])
    service = MemoryGraphProjectionService(extractor=FakeExtractor(), adapter=adapter)

    with pytest.raises(ProjectionVerificationError, match=str(EVIDENCE_ID)):
        service.project(request())


def test_existing_additional_provenance_does_not_fail_verification():
    other = provenance(
        assertion_id="55555555-5555-5555-8555-555555555555",
        evidence_id="66666666-6666-5666-8666-666666666666",
        predicate="located_in",
    )
    adapter = FakeAdapter(provenance_records=[provenance(), other])
    service = MemoryGraphProjectionService(extractor=FakeExtractor(), adapter=adapter)

    result = service.project(request())

    assert len(result.provenance) == 2


def test_request_preserves_exact_memory_text_for_evidence_hashing():
    projection_request = request(memory_text="  Alice works at Acme\n")
    adapter = FakeAdapter()
    service = MemoryGraphProjectionService(extractor=FakeExtractor(), adapter=adapter)

    service.project(projection_request)

    assert adapter.project_calls[0][1].excerpt_hash == excerpt_sha256("  Alice works at Acme\n")


def test_request_rejects_empty_memory_text_and_naive_recorded_at():
    with pytest.raises(ValidationError, match="memory_text"):
        request(memory_text="   ")
    with pytest.raises(ValidationError, match="recorded_at"):
        request(recorded_at="2026-07-21T00:00:00")
