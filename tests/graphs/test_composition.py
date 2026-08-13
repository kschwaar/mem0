import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mem0.graphs.backfill import BackfillStatus, GraphMemoryState
from mem0.graphs.composition import RelationshipGraphBackfill
from mem0.graphs.extractors import ExtractorIdentity
from mem0.graphs.models import (
    AssertionState,
    GraphScope,
    ProjectionResult,
    ProjectedEntity,
    RelationshipCandidate,
    RelationshipProvenance,
    assertion_id,
    entity_id,
    evidence_id,
)


class FakeVectorStore:
    collection_name = "memories"

    def __init__(self):
        self.calls = []
        self.rows = [
            SimpleNamespace(
                id="memory-1",
                payload={
                    "data": "Alice works at Acme",
                    "hash": "hash-1",
                    "user_id": "user-1",
                    "role": "user",
                },
            )
        ]

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return (self.rows, None)


class StaticExtractor:
    identity = ExtractorIdentity(name="composition-static", version="1", model_id="test-model")

    def __init__(self):
        self.calls = []

    def extract(self, memory_text):
        self.calls.append(memory_text)
        return [
            RelationshipCandidate(
                subject={"text": "Alice", "semantic_type": "PERSON"},
                predicate="works_at",
                object={"text": "Acme", "semantic_type": "ORG"},
                confidence=0.9,
            )
        ]


class InMemoryGraphAdapter:
    def __init__(self):
        self.records = {}
        self.inspections = []
        self.projection_calls = []

    def inspect(self, *, collection_name, scope, memory_id, memory_hash):
        self.inspections.append((collection_name, scope, memory_id, memory_hash))
        stored = self.records.get((collection_name, scope.key, memory_id))
        if stored is None:
            return GraphMemoryState.MISSING
        return GraphMemoryState.CURRENT if stored[0].memory_hash == memory_hash else GraphMemoryState.CONFLICT

    def project_relationships(self, relationships, source):
        self.projection_calls.append((relationships, source))
        projections = []
        provenance = []
        for relationship in relationships:
            projected_assertion_id = assertion_id(source.collection_name, source.scope, relationship)
            projected_evidence_id = evidence_id(projected_assertion_id, source)
            subject_entity_id = entity_id(source.collection_name, source.scope, relationship.subject)
            object_entity_id = entity_id(source.collection_name, source.scope, relationship.object)
            projections.append(
                ProjectionResult(
                    memory_id=source.memory_id,
                    subject_entity_id=subject_entity_id,
                    object_entity_id=object_entity_id,
                    assertion_id=projected_assertion_id,
                    evidence_id=projected_evidence_id,
                )
            )
            provenance.append(
                RelationshipProvenance(
                    collection_name=source.collection_name,
                    scope_key=source.scope.key,
                    assertion_id=projected_assertion_id,
                    subject=ProjectedEntity(
                        entity_id=subject_entity_id,
                        normalized_name=relationship.subject.normalized_name,
                        display_name=relationship.subject.text,
                        semantic_type=relationship.subject.semantic_type,
                    ),
                    predicate=relationship.predicate,
                    predicate_display=relationship.predicate_display or relationship.predicate,
                    object=ProjectedEntity(
                        entity_id=object_entity_id,
                        normalized_name=relationship.object.normalized_name,
                        display_name=relationship.object.text,
                        semantic_type=relationship.object.semantic_type,
                    ),
                    state=AssertionState.ACTIVE,
                    valid_from=relationship.valid_from,
                    valid_to=relationship.valid_to,
                    evidence_id=projected_evidence_id,
                    memory_id=source.memory_id,
                    memory_hash=source.memory_hash,
                    excerpt_hash=source.excerpt_hash,
                    confidence=relationship.confidence,
                    observed_at=relationship.observed_at,
                    recorded_at=source.recorded_at,
                    source_kind=source.source_kind,
                    projection_method=source.projection_method,
                    extractor_name=source.extractor_name,
                    extractor_version=source.extractor_version,
                    model_id=source.model_id,
                )
            )
        self.records[(source.collection_name, source.scope.key, source.memory_id)] = (source, provenance)
        return projections

    def provenance_by_memory(self, *, collection_name, scope, memory_id):
        stored = self.records.get((collection_name, scope.key, memory_id))
        return [] if stored is None else list(stored[1])


def composition(tmp_path):
    vector_store = FakeVectorStore()
    memory = SimpleNamespace(collection_name="memories", vector_store=vector_store)
    graph = InMemoryGraphAdapter()
    extractor = StaticExtractor()
    backfill = RelationshipGraphBackfill(
        memory=memory,
        graph=graph,
        extractor=extractor,
        checkpoint_directory=tmp_path,
        max_records=100,
    )
    return backfill, vector_store, graph, extractor


def test_composes_real_reader_projection_reconciliation_and_json_checkpoint(tmp_path):
    backfill, vector_store, graph, extractor = composition(tmp_path)
    scope = GraphScope(user_id="user-1")

    projected = backfill.run(run_id="first-run", scope=scope, page_size=10)
    reconciled = backfill.run(run_id="second-run", scope=scope, page_size=10)

    assert projected.status is BackfillStatus.COMPLETE
    assert projected.processed == 1
    assert projected.projected == 1
    assert reconciled.status is BackfillStatus.COMPLETE
    assert reconciled.processed == 1
    assert reconciled.skipped == 1
    assert extractor.calls == ["Alice works at Acme"]
    assert len(graph.projection_calls) == 1
    assert graph.projection_calls[0][1].projection_method.value == "BACKFILL"
    assert vector_store.calls == [
        {"filters": {"user_id": "user-1"}, "top_k": 101},
        {"filters": {"user_id": "user-1"}, "top_k": 101},
    ]

    checkpoint = json.loads((tmp_path / "first-run.json").read_text(encoding="utf-8"))
    assert checkpoint["completed_memory_ids"] == ["memory-1"]
    assert "Alice works at Acme" not in json.dumps(checkpoint)


def test_run_validates_request_before_reading_canonical_memory(tmp_path):
    backfill, vector_store, _, _ = composition(tmp_path)

    with pytest.raises((ValueError, ValidationError), match="run_id"):
        backfill.run(run_id="bad/run", scope=GraphScope(user_id="user-1"))
    with pytest.raises(ValidationError, match="page_size"):
        backfill.run(run_id="valid-run", scope=GraphScope(user_id="user-1"), page_size=0)

    assert vector_store.calls == []


def test_constructor_rejects_memory_and_vector_store_collection_mismatch(tmp_path):
    memory = SimpleNamespace(collection_name="other", vector_store=FakeVectorStore())

    with pytest.raises(ValueError, match="configured vector store"):
        RelationshipGraphBackfill(
            memory=memory,
            graph=InMemoryGraphAdapter(),
            extractor=StaticExtractor(),
            checkpoint_directory=tmp_path,
        )
