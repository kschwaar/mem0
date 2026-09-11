#!/usr/bin/env python3
"""Exercise live Neo4j projection, scoped lookup, replacement, and deletion."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from mem0.graphs.models import (
    EntityReference,
    GraphScope,
    ProjectionSource,
    RelationshipCandidate,
    SourceKind,
    excerpt_sha256,
)
from mem0.graphs.neo4j import Neo4jGraphConfig, Neo4jSchemaAdapter


def _source(collection_name: str, scope: GraphScope, *, memory_hash: str, excerpt: str) -> ProjectionSource:
    return ProjectionSource(
        collection_name=collection_name,
        scope=scope,
        memory_id="relationship-smoke-memory",
        memory_hash=memory_hash,
        excerpt_hash=excerpt_sha256(excerpt),
        source_kind=SourceKind.USER,
        extractor_name="relationship-smoke-test",
        extractor_version="1",
        model_id="deterministic-fixture",
        recorded_at=datetime.now(timezone.utc),
    )


def _relationship(company: str, confidence: float) -> RelationshipCandidate:
    return RelationshipCandidate(
        subject={"text": "Alice", "semantic_type": "PERSON"},
        predicate="works_at",
        predicate_display="works at",
        object={"text": company, "semantic_type": "ORG"},
        confidence=confidence,
        observed_at=datetime.now(timezone.utc),
    )


def main() -> None:
    collection_name = f"relationship-smoke-{uuid4()}"
    scope = GraphScope(user_id="relationship-smoke-user", app_id="mem0-local")
    other_scope = GraphScope(user_id="other-user", app_id="mem0-local")
    memory_id = "relationship-smoke-memory"
    adapter = Neo4jSchemaAdapter.connect(
        Neo4jGraphConfig(
            uri=os.environ.get("MEM0_GRAPH_NEO4J_URI")
            or os.environ.get("NEO4J_URL", "bolt://neo4j-mem0:7687"),
            username=os.environ.get("MEM0_GRAPH_NEO4J_USERNAME")
            or os.environ.get("NEO4J_USERNAME", "neo4j"),
            password=os.environ.get("MEM0_GRAPH_NEO4J_PASSWORD") or os.environ["NEO4J_PASSWORD"],
            database=os.environ.get("MEM0_GRAPH_NEO4J_DATABASE", "neo4j"),
            query_timeout_seconds=float(os.environ.get("MEM0_GRAPH_QUERY_TIMEOUT_SECONDS", "5")),
        )
    )
    try:
        adapter.bootstrap_schema()
        first_source = _source(
            collection_name,
            scope,
            memory_hash="hash-v1",
            excerpt="Alice works at Acme",
        )
        first_projection = adapter.project_relationship(_relationship("Acme", 0.91), first_source)
        replayed_projection = adapter.project_relationship(_relationship("Acme", 0.91), first_source)
        assert replayed_projection == first_projection

        alice = [EntityReference(text="Alice", semantic_type="PERSON")]
        initial_signals = adapter.candidate_signals(
            collection_name=collection_name,
            scope=scope,
            query_entities=alice,
            candidate_memory_ids=[memory_id, "not-a-semantic-candidate"],
            explanation_limit=3,
        )
        assert len(initial_signals) == 1
        assert initial_signals[0].memory_id == memory_id
        assert initial_signals[0].graph_score == 0.91
        assert initial_signals[0].explanations[0].object.display_name == "Acme"
        assert (
            adapter.candidate_signals(
                collection_name=collection_name,
                scope=other_scope,
                query_entities=alice,
                candidate_memory_ids=[memory_id],
                explanation_limit=3,
            )
            == []
        )

        replacement_source = _source(
            collection_name,
            scope,
            memory_hash="hash-v2",
            excerpt="Alice works at Beta",
        )
        replacement = adapter.replace_relationships(
            [_relationship("Beta", 0.97)],
            replacement_source,
            previous_hash="hash-v1",
        )
        assert replacement.evidence_deleted == 1
        assert replacement.assertions_retracted == 1

        beta_signals = adapter.candidate_signals(
            collection_name=collection_name,
            scope=scope,
            query_entities=[EntityReference(text="Beta", semantic_type="ORG")],
            candidate_memory_ids=[memory_id],
            explanation_limit=3,
        )
        assert len(beta_signals) == 1
        assert beta_signals[0].graph_score == 0.97
        assert beta_signals[0].explanations[0].object.display_name == "Beta"

        deletion = adapter.delete_memory(
            collection_name=collection_name,
            scope=scope,
            memory_id=memory_id,
            memory_hash="hash-v2",
            deleted_at=datetime.now(timezone.utc),
        )
        assert deletion.evidence_deleted == 1
        assert deletion.assertions_retracted == 1
        assert (
            adapter.candidate_signals(
                collection_name=collection_name,
                scope=scope,
                query_entities=alice,
                candidate_memory_ids=[memory_id],
                explanation_limit=3,
            )
            == []
        )

        print(
            json.dumps(
                {
                    "status": "passed",
                    "idempotent_projection": True,
                    "exact_scope_isolation": True,
                    "semantic_candidate_bound": True,
                    "replacement": {
                        "evidence_deleted": replacement.evidence_deleted,
                        "assertions_retracted": replacement.assertions_retracted,
                    },
                    "delete": {
                        "evidence_deleted": deletion.evidence_deleted,
                        "assertions_retracted": deletion.assertions_retracted,
                    },
                },
                indent=2,
            )
        )
    finally:
        adapter.reset_collection(collection_name)
        adapter.close()


if __name__ == "__main__":
    main()
