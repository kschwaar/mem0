"""Opt-in Neo4j 5 integration test.

Set MEM0_TEST_NEO4J_URI, MEM0_TEST_NEO4J_USERNAME and
MEM0_TEST_NEO4J_PASSWORD to run this test against a disposable database.
"""

import os

import pytest

from mem0.graphs.extractors import ExtractorIdentity, ValidatedRelationshipExtractor
from mem0.graphs.models import GraphMemoryState, ProjectionSource, RelationshipCandidate, SourceKind, excerpt_sha256
from mem0.graphs.neo4j import NEO4J_SCHEMA_OBJECT_NAMES, Neo4jGraphConfig, Neo4jSchemaAdapter, ProjectionConflictError
from mem0.graphs.service import MemoryGraphProjectionRequest, MemoryGraphProjectionService


pytestmark = pytest.mark.skipif(
    not os.environ.get("MEM0_TEST_NEO4J_URI"),
    reason="MEM0_TEST_NEO4J_URI is not configured",
)


def test_schema_bootstrap_is_idempotent_against_neo4j_5():
    pytest.importorskip("neo4j")
    graph_config = Neo4jGraphConfig(
        uri=os.environ["MEM0_TEST_NEO4J_URI"],
        username=os.environ.get("MEM0_TEST_NEO4J_USERNAME", "neo4j"),
        password=os.environ["MEM0_TEST_NEO4J_PASSWORD"],
        database=os.environ.get("MEM0_TEST_NEO4J_DATABASE", "neo4j"),
    )

    with Neo4jSchemaAdapter.connect(graph_config) as adapter:
        adapter.bootstrap_schema()
        adapter.bootstrap_schema()
        with adapter._driver.session(database=graph_config.database) as session:
            names = {record["name"] for record in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}
            names.update(record["name"] for record in session.run("SHOW INDEXES YIELD name RETURN name"))

    assert NEO4J_SCHEMA_OBJECT_NAMES <= names


def test_relationship_projection_is_atomic_and_idempotent_against_neo4j_5():
    pytest.importorskip("neo4j")
    graph_config = Neo4jGraphConfig(
        uri=os.environ["MEM0_TEST_NEO4J_URI"],
        username=os.environ.get("MEM0_TEST_NEO4J_USERNAME", "neo4j"),
        password=os.environ["MEM0_TEST_NEO4J_PASSWORD"],
        database=os.environ.get("MEM0_TEST_NEO4J_DATABASE", "neo4j"),
    )
    relationship = RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Acme", "semantic_type": "ORG"},
        confidence=0.91,
    )
    source = ProjectionSource(
        collection_name="integration-test",
        scope={"user_id": "projection-user"},
        memory_id="projection-memory",
        memory_hash="hash-1",
        excerpt_hash=excerpt_sha256("Alice works at Acme"),
        source_kind=SourceKind.USER,
        extractor_name="test-extractor",
        extractor_version="1",
    )

    with Neo4jSchemaAdapter.connect(graph_config) as adapter:
        adapter.bootstrap_schema()
        first = adapter.project_relationship(relationship, source)
        second = adapter.project_relationship(relationship, source)
        assert first == second

        by_assertion = adapter.provenance_by_assertion(
            collection_name=source.collection_name,
            scope=source.scope,
            assertion=first.assertion_id,
        )
        by_memory = adapter.provenance_by_memory(
            collection_name=source.collection_name,
            scope=source.scope,
            memory_id=source.memory_id,
        )
        wrong_scope = adapter.provenance_by_memory(
            collection_name=source.collection_name,
            scope=source.scope.model_copy(update={"user_id": "another-user"}),
            memory_id=source.memory_id,
        )

        assert by_assertion == by_memory
        assert len(by_assertion) == 1
        assert by_assertion[0].assertion_id == first.assertion_id
        assert by_assertion[0].memory_id == source.memory_id
        assert wrong_scope == []

        with adapter._driver.session(database=graph_config.database) as session:
            record = session.run(
                """
                MATCH (memory:Mem0Memory {collection_name: $collection, scope_key: $scope_key})
                MATCH (subject:Mem0Entity)-[:SUBJECT_OF]->(assertion:RelationshipAssertion)
                      -[:SUPPORTED_BY]->(evidence:Evidence)-[:FROM_MEMORY]->(memory)
                MATCH (assertion)-[:OBJECT_OF]->(object:Mem0Entity)
                RETURN count(DISTINCT memory) AS memories,
                       count(DISTINCT subject) + count(DISTINCT object) AS entities,
                       count(DISTINCT assertion) AS assertions,
                       count(DISTINCT evidence) AS evidence
                """,
                collection=source.collection_name,
                scope_key=source.scope.key,
            ).single()
        assert dict(record) == {"memories": 1, "entities": 2, "assertions": 1, "evidence": 1}

        with pytest.raises(ProjectionConflictError):
            adapter.project_relationship(relationship, source.model_copy(update={"memory_hash": "hash-2"}))


def test_manual_vertical_slice_against_neo4j_5():
    pytest.importorskip("neo4j")

    class StaticBackend:
        def extract(self, memory_text):
            return {
                "relationships": [
                    {
                        "subject": {"text": "Alice", "semantic_type": "PERSON"},
                        "predicate": "works_at",
                        "object": {"text": "Acme", "semantic_type": "ORG"},
                        "confidence": 0.91,
                    }
                ]
            }

    graph_config = Neo4jGraphConfig(
        uri=os.environ["MEM0_TEST_NEO4J_URI"],
        username=os.environ.get("MEM0_TEST_NEO4J_USERNAME", "neo4j"),
        password=os.environ["MEM0_TEST_NEO4J_PASSWORD"],
        database=os.environ.get("MEM0_TEST_NEO4J_DATABASE", "neo4j"),
    )
    extractor = ValidatedRelationshipExtractor(
        identity=ExtractorIdentity(name="integration-static", version="1"),
        backend=StaticBackend(),
    )
    request = MemoryGraphProjectionRequest(
        memory_text="Alice works at Acme",
        collection_name="vertical-slice-test",
        scope={"user_id": "vertical-slice-user"},
        memory_id="vertical-slice-memory",
        memory_hash="vertical-slice-hash",
        source_kind=SourceKind.USER,
    )

    with Neo4jSchemaAdapter.connect(graph_config) as adapter:
        adapter.bootstrap_schema()
        service = MemoryGraphProjectionService(extractor=extractor, adapter=adapter)
        first = service.project(request)
        second = service.project(request)

    assert first == second
    assert len(first.projections) == 1
    assert len(first.provenance) == 1
    assert first.provenance[0].subject.display_name == "Alice"
    assert first.provenance[0].predicate == "works_at"
    assert first.provenance[0].object.display_name == "Acme"
    assert first.provenance[0].memory_id == request.memory_id


def test_memory_inspection_classifies_normal_and_malformed_records_against_neo4j_5():
    pytest.importorskip("neo4j")
    graph_config = Neo4jGraphConfig(
        uri=os.environ["MEM0_TEST_NEO4J_URI"],
        username=os.environ.get("MEM0_TEST_NEO4J_USERNAME", "neo4j"),
        password=os.environ["MEM0_TEST_NEO4J_PASSWORD"],
        database=os.environ.get("MEM0_TEST_NEO4J_DATABASE", "neo4j"),
    )
    scope = {"user_id": "inspection-user"}
    source = ProjectionSource(
        collection_name="inspection-test",
        scope=scope,
        memory_id="inspection-memory",
        memory_hash="inspection-hash",
        excerpt_hash=excerpt_sha256("Alice works at Acme"),
        source_kind=SourceKind.IMPORTED,
        extractor_name="test-extractor",
        extractor_version="1",
    )
    relationship = RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        object={"text": "Acme", "semantic_type": "ORG"},
    )

    with Neo4jSchemaAdapter.connect(graph_config) as adapter:
        adapter.bootstrap_schema()
        assert (
            adapter.inspect(
                collection_name=source.collection_name,
                scope=source.scope,
                memory_id="missing-memory",
                memory_hash=source.memory_hash,
            )
            is GraphMemoryState.MISSING
        )

        adapter.project_relationship(relationship, source)
        assert (
            adapter.inspect(
                collection_name=source.collection_name,
                scope=source.scope,
                memory_id=source.memory_id,
                memory_hash=source.memory_hash,
            )
            is GraphMemoryState.CURRENT
        )
        assert (
            adapter.inspect(
                collection_name=source.collection_name,
                scope=source.scope,
                memory_id=source.memory_id,
                memory_hash="changed-hash",
            )
            is GraphMemoryState.CONFLICT
        )

        with adapter._driver.session(database=graph_config.database) as session:
            session.run(
                """
                MATCH (:Mem0Entity)-[edge:SUBJECT_OF]->(assertion:RelationshipAssertion)
                      -[:SUPPORTED_BY]->(:Evidence)-[:FROM_MEMORY]->(memory:Mem0Memory {
                          collection_name: $collection_name,
                          scope_key: $scope_key,
                          memory_id: $memory_id
                      })
                DELETE edge
                """,
                collection_name=source.collection_name,
                scope_key=source.scope.key,
                memory_id=source.memory_id,
            ).consume()

        assert (
            adapter.inspect(
                collection_name=source.collection_name,
                scope=source.scope,
                memory_id=source.memory_id,
                memory_hash=source.memory_hash,
            )
            is GraphMemoryState.INCOMPLETE
        )
