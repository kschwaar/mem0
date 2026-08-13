from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mem0.graphs.models import (
    EntityReference,
    GraphScope,
    ProjectionMethod,
    ProjectionSource,
    RelationshipCandidate,
    SourceKind,
    assertion_dedupe_key,
    assertion_id,
    entity_id,
    evidence_id,
    excerpt_sha256,
)


def relationship(**overrides):
    values = {
        "subject": {"text": " Alice ", "semantic_type": "person"},
        "predicate": "Works At",
        "object": {"text": "Acme", "semantic_type": "ORG"},
        "confidence": 0.91,
        "observed_at": "2026-07-20T00:00:00Z",
    }
    values.update(overrides)
    return RelationshipCandidate(**values)


def projection_source(**overrides):
    values = {
        "collection_name": "memories",
        "scope": {"user_id": "user-1"},
        "memory_id": "memory-1",
        "memory_hash": "memory-hash",
        "excerpt_hash": excerpt_sha256("Alice works at Acme"),
        "source_kind": SourceKind.USER,
        "projection_method": ProjectionMethod.MANUAL,
        "extractor_name": "relationship-extractor",
        "extractor_version": "1",
        "model_id": "test-model",
    }
    values.update(overrides)
    return ProjectionSource(**values)


def test_scope_key_is_stable_and_includes_null_dimensions():
    first = GraphScope(user_id=" user-1 ")
    second = GraphScope(user_id="user-1", agent_id=None, app_id=None, run_id=None)

    assert first.canonical_values() == {
        "user_id": "user-1",
        "agent_id": None,
        "app_id": None,
        "run_id": None,
    }
    assert first.key == second.key
    assert len(first.key) == 64


def test_scope_requires_at_least_one_identifier_and_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="at least one"):
        GraphScope()
    with pytest.raises(ValidationError, match="Extra inputs"):
        GraphScope(user_id="user-1", organization_id="org-1")


def test_different_complete_scopes_have_different_keys():
    user_scope = GraphScope(user_id="user-1")
    run_scope = GraphScope(user_id="user-1", run_id="run-1")

    assert user_scope.key != run_scope.key


def test_entity_reference_normalizes_display_and_identity_values():
    entity = EntityReference(text="  ALICE\u00a0  SMITH ", semantic_type="person-role")

    assert entity.text == "ALICE SMITH"
    assert entity.normalized_name == "alice smith"
    assert entity.semantic_type == "PERSON_ROLE"


@pytest.mark.parametrize("semantic_type", ["", "PERSON!", "_PERSON", "PERSON__ROLE"])
def test_entity_reference_rejects_invalid_semantic_types(semantic_type):
    with pytest.raises(ValidationError):
        EntityReference(text="Alice", semantic_type=semantic_type)


def test_relationship_normalizes_predicate_and_requires_timezone():
    candidate = relationship(predicate=" Works-At ")

    assert candidate.predicate == "works_at"
    assert candidate.observed_at == datetime(2026, 7, 20, tzinfo=timezone.utc)

    with pytest.raises(ValidationError, match="timezone"):
        relationship(observed_at="2026-07-20T00:00:00")


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_relationship_rejects_invalid_confidence(confidence):
    with pytest.raises(ValidationError):
        relationship(confidence=confidence)


def test_relationship_rejects_self_loop_unless_explicitly_allowed():
    with pytest.raises(ValidationError, match="self-loop"):
        relationship(object={"text": "alice", "semantic_type": "PERSON"})

    candidate = relationship(
        object={"text": "alice", "semantic_type": "PERSON"},
        allow_self_loop=True,
    )
    assert candidate.allow_self_loop is True


def test_relationship_rejects_reversed_validity_interval():
    with pytest.raises(ValidationError, match="valid_to"):
        relationship(
            valid_from="2026-07-21T00:00:00Z",
            valid_to="2026-07-20T00:00:00Z",
        )


def test_projection_source_validates_and_normalizes_excerpt_hash():
    source = projection_source(excerpt_hash="A" * 64)

    assert source.excerpt_hash == "a" * 64
    assert source.projection_method is ProjectionMethod.MANUAL

    with pytest.raises(ValidationError, match="SHA-256"):
        projection_source(excerpt_hash="not-a-sha256")


def test_assertion_identity_is_stable_after_input_normalization():
    scope = GraphScope(user_id="user-1")
    first = relationship()
    second = relationship(
        subject={"text": "alice", "semantic_type": "PERSON"},
        predicate="works_at",
    )

    assert assertion_dedupe_key("memories", scope, first) == assertion_dedupe_key("memories", scope, second)
    assert assertion_id("memories", scope, first) == assertion_id("memories", scope, second)


def test_entity_identity_is_stable_and_isolated_by_scope_and_collection():
    entity = EntityReference(text="Alice", semantic_type="PERSON")
    normalized = EntityReference(text=" alice ", semantic_type="person")
    scope = GraphScope(user_id="user-1")

    assert entity_id("memories", scope, entity) == entity_id("memories", scope, normalized)
    assert entity_id("memories", scope, entity) != entity_id("memories", GraphScope(user_id="user-2"), entity)
    assert entity_id("memories", scope, entity) != entity_id("other-memories", scope, entity)


def test_assertion_identity_isolated_by_scope_collection_and_validity():
    candidate = relationship()
    base_scope = GraphScope(user_id="user-1")
    other_scope = GraphScope(user_id="user-2")
    dated = relationship(valid_from="2026-07-20T00:00:00Z")

    identities = {
        assertion_id("memories", base_scope, candidate),
        assertion_id("other-memories", base_scope, candidate),
        assertion_id("memories", other_scope, candidate),
        assertion_id("memories", base_scope, dated),
    }
    assert len(identities) == 4


def test_evidence_identity_is_stable_but_changes_with_source_version():
    candidate_id = assertion_id("memories", GraphScope(user_id="user-1"), relationship())
    source = projection_source()

    assert evidence_id(candidate_id, source) == evidence_id(candidate_id, projection_source())
    assert evidence_id(candidate_id, source) != evidence_id(
        candidate_id,
        projection_source(extractor_version="2"),
    )
    assert evidence_id(candidate_id, source) != evidence_id(
        candidate_id,
        projection_source(model_id="other-model"),
    )


def test_projection_source_requires_timezone_aware_recorded_at():
    with pytest.raises(ValidationError, match="recorded_at"):
        projection_source(recorded_at="2026-07-20T00:00:00")
