from copy import deepcopy

import pytest
from pydantic import ValidationError

from mem0.graphs.extractors import (
    ExtractorIdentity,
    RelationshipExtractionError,
    RelationshipExtractor,
    StructuredRelationshipBackend,
    ValidatedRelationshipExtractor,
)


def raw_relationship(**overrides):
    values = {
        "subject": {"text": " Alice ", "semantic_type": "person"},
        "predicate": "Works At",
        "predicate_display": "works at",
        "object": {"text": "Acme", "semantic_type": "ORG"},
        "confidence": 0.91,
        "observed_at": "2026-07-20T00:00:00Z",
    }
    values.update(overrides)
    return values


class StaticBackend:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def extract(self, memory_text):
        self.calls.append(memory_text)
        return deepcopy(self.payload)


class FailingBackend:
    def extract(self, memory_text):
        raise RuntimeError("provider unavailable")


def extractor(payload, *, max_relationships=50, backend=None):
    selected_backend = backend or StaticBackend(payload)
    return (
        ValidatedRelationshipExtractor(
            identity=ExtractorIdentity(name="manual-structured", version="1", model_id="test-model"),
            backend=selected_backend,
            max_relationships=max_relationships,
        ),
        selected_backend,
    )


def test_structured_backend_is_validated_and_normalized():
    service, backend = extractor({"relationships": [raw_relationship()]})

    candidates = service.extract("Alice works at Acme")

    assert backend.calls == ["Alice works at Acme"]
    assert len(candidates) == 1
    assert candidates[0].subject.text == "Alice"
    assert candidates[0].subject.semantic_type == "PERSON"
    assert candidates[0].predicate == "works_at"
    assert service.identity == ExtractorIdentity(name="manual-structured", version="1", model_id="test-model")
    assert isinstance(service, RelationshipExtractor)
    assert isinstance(backend, StructuredRelationshipBackend)


def test_empty_relationship_payload_is_valid():
    service, _ = extractor({"relationships": []})

    assert service.extract("No relationship is present") == []


def test_repeated_deterministic_payload_produces_equal_candidates():
    service, _ = extractor({"relationships": [raw_relationship()]})

    assert service.extract("Alice works at Acme") == service.extract("Alice works at Acme")


@pytest.mark.parametrize("memory_text", ["", "   ", None, 42])
def test_invalid_memory_text_is_rejected_before_backend_call(memory_text):
    service, backend = extractor({"relationships": []})

    with pytest.raises(ValueError, match="memory_text"):
        service.extract(memory_text)

    assert backend.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"relations": []},
        {"relationships": [], "unexpected": True},
        {"relationships": "not-a-list"},
    ],
)
def test_malformed_top_level_payload_is_rejected(payload):
    service, _ = extractor(payload)

    with pytest.raises(RelationshipExtractionError, match="invalid payload"):
        service.extract("Alice works at Acme")


def test_one_invalid_relationship_rejects_the_complete_batch():
    service, _ = extractor(
        {
            "relationships": [
                raw_relationship(),
                raw_relationship(confidence=2.0),
            ]
        }
    )

    with pytest.raises(RelationshipExtractionError, match="invalid payload") as captured:
        service.extract("Alice works at Acme")

    assert captured.value.__cause__ is not None
    assert "relationships.1.confidence" in str(captured.value.__cause__)


def test_relationship_limit_rejects_the_complete_batch():
    service, _ = extractor(
        {"relationships": [raw_relationship(), raw_relationship(predicate="located_in")]},
        max_relationships=1,
    )

    with pytest.raises(RelationshipExtractionError, match="returned 2 relationships; maximum is 1"):
        service.extract("Alice works at Acme")


@pytest.mark.parametrize("maximum", [0, -1, 1.5, True])
def test_relationship_limit_must_be_a_positive_integer(maximum):
    with pytest.raises(ValueError, match="positive integer"):
        extractor({"relationships": []}, max_relationships=maximum)


def test_backend_failure_is_wrapped_without_exposing_memory_text():
    service, _ = extractor({}, backend=FailingBackend())
    memory_text = "private relationship content"

    with pytest.raises(RelationshipExtractionError, match="backend 'manual-structured' failed") as captured:
        service.extract(memory_text)

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert memory_text not in str(captured.value)


def test_extractor_identity_is_strict_and_redacts_no_hidden_state():
    identity = ExtractorIdentity(name=" extractor ", version=" 1 ")

    assert identity.name == "extractor"
    assert identity.version == "1"
    with pytest.raises(ValidationError):
        ExtractorIdentity(name="extractor", version="1", provider_secret="secret")
