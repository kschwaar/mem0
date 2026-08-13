"""Neo4j connection boundary and Community-compatible schema bootstrap."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Type
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from mem0.graphs.models import (
    EntityReference,
    GraphLifecycleMutation,
    GraphMemoryState,
    GraphScope,
    GraphUpdateMutation,
    ProjectionMethod,
    ProjectionResult,
    ProjectionSource,
    RelationshipCandidate,
    RelationshipProvenance,
    assertion_dedupe_key,
    assertion_id,
    entity_id,
    evidence_id,
)
from mem0.graphs.outbox import (
    ProjectionEvent,
    ProjectionEventApplication,
    ProjectionEventOperation,
    ProjectionEventPayload,
)
from mem0.graphs.retrieval import GraphCandidateSignal, GraphSearchExplanation

NEO4J_SCHEMA_STATEMENTS = (
    """
    CREATE CONSTRAINT mem0_projection_event_id IF NOT EXISTS
    FOR (event:ProjectionEvent)
    REQUIRE event.event_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_memory_scope_id IF NOT EXISTS
    FOR (memory:Mem0Memory)
    REQUIRE (memory.collection_name, memory.scope_key, memory.memory_id) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_entity_id IF NOT EXISTS
    FOR (entity:Mem0Entity)
    REQUIRE entity.entity_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_entity_scope_key IF NOT EXISTS
    FOR (entity:Mem0Entity)
    REQUIRE (
        entity.scope_key,
        entity.collection_name,
        entity.normalized_name,
        entity.semantic_type
    ) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_assertion_id IF NOT EXISTS
    FOR (assertion:RelationshipAssertion)
    REQUIRE assertion.assertion_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_assertion_dedupe IF NOT EXISTS
    FOR (assertion:RelationshipAssertion)
    REQUIRE assertion.dedupe_key IS UNIQUE
    """,
    """
    CREATE CONSTRAINT mem0_evidence_id IF NOT EXISTS
    FOR (evidence:Evidence)
    REQUIRE evidence.evidence_id IS UNIQUE
    """,
    """
    CREATE INDEX mem0_assertion_scope_state IF NOT EXISTS
    FOR (assertion:RelationshipAssertion)
    ON (assertion.collection_name, assertion.scope_key, assertion.state)
    """,
)

NEO4J_SCHEMA_OBJECT_NAMES = frozenset(
    {
        "mem0_memory_scope_id",
        "mem0_projection_event_id",
        "mem0_entity_id",
        "mem0_entity_scope_key",
        "mem0_assertion_id",
        "mem0_assertion_dedupe",
        "mem0_evidence_id",
        "mem0_assertion_scope_state",
    }
)

READ_PROJECTION_EVENT_QUERY = """
MATCH (event:ProjectionEvent {event_id: $event_id})
RETURN
    event.event_id AS event_id,
    event.operation AS operation,
    event.collection_name AS collection_name,
    event.scope_key AS scope_key,
    event.memory_id AS memory_id,
    event.memory_hash AS memory_hash,
    event.previous_hash AS previous_hash,
    event.source_kind AS source_kind,
    event.occurred_at AS occurred_at,
    event.payload_hash AS payload_hash
"""

RECORD_PROJECTION_EVENT_QUERY = """
CREATE (event:ProjectionEvent {
    event_id: $event_id,
    operation: $operation,
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id,
    memory_hash: $memory_hash,
    previous_hash: $previous_hash,
    source_kind: $source_kind,
    occurred_at: $occurred_at,
    payload_hash: $payload_hash,
    applied_at: $applied_at
})
WITH event
OPTIONAL MATCH (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
FOREACH (target IN CASE WHEN memory IS NULL THEN [] ELSE [memory] END |
    MERGE (event)-[:FOR_MEMORY]->(target)
)
RETURN event.event_id AS event_id
"""

READ_CANDIDATE_SIGNALS_QUERY = """
UNWIND $candidate_memory_ids AS candidate_memory_id
CALL {
    WITH candidate_memory_id
    MATCH (subject:Mem0Entity)-[:SUBJECT_OF]->(assertion:RelationshipAssertion)
          -[:OBJECT_OF]->(object:Mem0Entity)
    WHERE assertion.collection_name = $collection_name
      AND assertion.scope_key = $scope_key
      AND assertion.state = 'ACTIVE'
      AND subject.collection_name = $collection_name
      AND subject.scope_key = $scope_key
      AND object.collection_name = $collection_name
      AND object.scope_key = $scope_key
      AND any(query_entity IN $query_entities WHERE
          (subject.normalized_name = query_entity.normalized_name
           AND subject.semantic_type = query_entity.semantic_type)
          OR
          (object.normalized_name = query_entity.normalized_name
           AND object.semantic_type = query_entity.semantic_type)
      )
    MATCH (assertion)-[:SUPPORTED_BY]->(evidence:Evidence)-[:FROM_MEMORY]->(memory:Mem0Memory)
    WHERE memory.collection_name = $collection_name
      AND memory.scope_key = $scope_key
      AND memory.memory_id = candidate_memory_id
      AND memory.deleted_at IS NULL
      AND evidence.memory_hash = memory.memory_hash
    WITH
        memory.memory_id AS memory_id,
        assertion,
        subject,
        object,
        max(evidence.confidence) AS confidence
    ORDER BY confidence DESC, assertion.assertion_id ASC
    LIMIT $explanation_limit
    RETURN memory_id, assertion, subject, object, confidence
}
RETURN
    memory_id,
    assertion.assertion_id AS assertion_id,
    {
        entity_id: subject.entity_id,
        normalized_name: subject.normalized_name,
        display_name: subject.display_name,
        semantic_type: subject.semantic_type
    } AS subject,
    assertion.predicate AS predicate,
    assertion.predicate_display AS predicate_display,
    {
        entity_id: object.entity_id,
        normalized_name: object.normalized_name,
        display_name: object.display_name,
        semantic_type: object.semantic_type
    } AS object,
    confidence
ORDER BY memory_id ASC, confidence DESC, assertion_id ASC
"""

DELETE_COLLECTION_EVIDENCE_QUERY = """
MATCH (evidence:Evidence)-[:FROM_MEMORY]->(memory:Mem0Memory {collection_name: $collection_name})
DETACH DELETE evidence
RETURN count(evidence) AS evidence_deleted
"""

DELETE_COLLECTION_NODES_QUERY = """
MATCH (node)
WHERE node.collection_name = $collection_name
  AND (node:Mem0Memory OR node:Mem0Entity OR node:RelationshipAssertion OR node:ProjectionEvent)
DETACH DELETE node
RETURN count(node) AS nodes_deleted
"""

PROJECT_RELATIONSHIP_QUERY = """
MERGE (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
ON CREATE SET
    memory.memory_hash = $memory_hash,
    memory.created_at = $recorded_at,
    memory.updated_at = $recorded_at
WITH memory
WHERE memory.memory_hash = $memory_hash
MERGE (subject:Mem0Entity {entity_id: $subject_entity_id})
ON CREATE SET
    subject.collection_name = $collection_name,
    subject.scope_key = $scope_key,
    subject.normalized_name = $subject_normalized_name,
    subject.display_name = $subject_display_name,
    subject.semantic_type = $subject_semantic_type,
    subject.created_at = $recorded_at,
    subject.updated_at = $recorded_at
MERGE (object:Mem0Entity {entity_id: $object_entity_id})
ON CREATE SET
    object.collection_name = $collection_name,
    object.scope_key = $scope_key,
    object.normalized_name = $object_normalized_name,
    object.display_name = $object_display_name,
    object.semantic_type = $object_semantic_type,
    object.created_at = $recorded_at,
    object.updated_at = $recorded_at
MERGE (assertion:RelationshipAssertion {dedupe_key: $dedupe_key})
ON CREATE SET
    assertion.assertion_id = $assertion_id,
    assertion.collection_name = $collection_name,
    assertion.scope_key = $scope_key,
    assertion.predicate = $predicate,
    assertion.predicate_display = $predicate_display,
    assertion.state = 'ACTIVE',
    assertion.valid_from = $valid_from,
    assertion.valid_to = $valid_to,
    assertion.created_at = $recorded_at,
    assertion.updated_at = $recorded_at
ON MATCH SET
    assertion.invalidated_at = CASE
        WHEN assertion.state = 'RETRACTED' THEN null
        ELSE assertion.invalidated_at
    END,
    assertion.state = CASE
        WHEN assertion.state = 'RETRACTED' THEN 'ACTIVE'
        ELSE assertion.state
    END,
    assertion.updated_at = $recorded_at
MERGE (evidence:Evidence {evidence_id: $evidence_id})
ON CREATE SET
    evidence.collection_name = $collection_name,
    evidence.scope_key = $scope_key,
    evidence.memory_id = $memory_id,
    evidence.memory_hash = $memory_hash,
    evidence.excerpt_hash = $excerpt_hash,
    evidence.confidence = $confidence,
    evidence.observed_at = $observed_at,
    evidence.recorded_at = $recorded_at,
    evidence.source_kind = $source_kind,
    evidence.projection_method = $projection_method,
    evidence.extractor_name = $extractor_name,
    evidence.extractor_version = $extractor_version,
    evidence.model_id = $model_id
MERGE (subject)-[:SUBJECT_OF]->(assertion)
MERGE (assertion)-[:OBJECT_OF]->(object)
MERGE (assertion)-[:SUPPORTED_BY]->(evidence)
MERGE (evidence)-[:FROM_MEMORY]->(memory)
RETURN
    memory.memory_id AS memory_id,
    subject.entity_id AS subject_entity_id,
    object.entity_id AS object_entity_id,
    assertion.assertion_id AS assertion_id,
    evidence.evidence_id AS evidence_id
"""

_PROVENANCE_MATCH = """
MATCH (subject:Mem0Entity)-[:SUBJECT_OF]->(assertion:RelationshipAssertion)
      -[:OBJECT_OF]->(object:Mem0Entity)
MATCH (assertion)-[:SUPPORTED_BY]->(evidence:Evidence)-[:FROM_MEMORY]->(memory:Mem0Memory)
WHERE assertion.collection_name = $collection_name
  AND assertion.scope_key = $scope_key
  AND subject.collection_name = $collection_name
  AND subject.scope_key = $scope_key
  AND object.collection_name = $collection_name
  AND object.scope_key = $scope_key
  AND memory.collection_name = $collection_name
  AND memory.scope_key = $scope_key
"""

_PROVENANCE_RETURN = """
RETURN
    assertion.collection_name AS collection_name,
    assertion.scope_key AS scope_key,
    assertion.assertion_id AS assertion_id,
    {
        entity_id: subject.entity_id,
        normalized_name: subject.normalized_name,
        display_name: subject.display_name,
        semantic_type: subject.semantic_type
    } AS subject,
    assertion.predicate AS predicate,
    assertion.predicate_display AS predicate_display,
    {
        entity_id: object.entity_id,
        normalized_name: object.normalized_name,
        display_name: object.display_name,
        semantic_type: object.semantic_type
    } AS object,
    assertion.state AS state,
    assertion.valid_from AS valid_from,
    assertion.valid_to AS valid_to,
    evidence.evidence_id AS evidence_id,
    memory.memory_id AS memory_id,
    memory.memory_hash AS memory_hash,
    evidence.excerpt_hash AS excerpt_hash,
    evidence.confidence AS confidence,
    evidence.observed_at AS observed_at,
    evidence.recorded_at AS recorded_at,
    evidence.source_kind AS source_kind,
    evidence.projection_method AS projection_method,
    evidence.extractor_name AS extractor_name,
    evidence.extractor_version AS extractor_version,
    evidence.model_id AS model_id
ORDER BY evidence.recorded_at ASC, evidence.evidence_id ASC
"""

READ_PROVENANCE_BY_ASSERTION_QUERY = (
    _PROVENANCE_MATCH + "  AND assertion.assertion_id = $assertion_id\n" + _PROVENANCE_RETURN
)

READ_PROVENANCE_BY_MEMORY_QUERY = _PROVENANCE_MATCH + "  AND memory.memory_id = $memory_id\n" + _PROVENANCE_RETURN

INSPECT_MEMORY_QUERY = """
OPTIONAL MATCH (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
WITH collect(memory) AS memories
UNWIND CASE WHEN size(memories) = 0 THEN [null] ELSE memories END AS memory
OPTIONAL MATCH (evidence:Evidence)-[:FROM_MEMORY]->(memory)
WITH memories, collect(DISTINCT evidence) AS evidence_records
UNWIND CASE WHEN size(evidence_records) = 0 THEN [null] ELSE evidence_records END AS evidence
OPTIONAL MATCH (assertion:RelationshipAssertion)-[:SUPPORTED_BY]->(evidence)
OPTIONAL MATCH (subject:Mem0Entity)-[:SUBJECT_OF]->(assertion)
OPTIONAL MATCH (assertion)-[:OBJECT_OF]->(object:Mem0Entity)
WITH
    memories,
    evidence_records,
    collect(DISTINCT assertion) AS assertions,
    collect(DISTINCT CASE WHEN assertion IS NOT NULL THEN evidence END) AS linked_evidence,
    collect(DISTINCT CASE
        WHEN assertion IS NOT NULL
          AND subject IS NOT NULL
          AND object IS NOT NULL
          AND assertion.collection_name = $collection_name
          AND assertion.scope_key = $scope_key
          AND subject.collection_name = $collection_name
          AND subject.scope_key = $scope_key
          AND object.collection_name = $collection_name
          AND object.scope_key = $scope_key
        THEN assertion
    END) AS complete_assertions
RETURN
    size(memories) AS memory_count,
    CASE WHEN size(memories) = 1 THEN memories[0].memory_hash ELSE null END AS memory_hash,
    size(evidence_records) AS evidence_count,
    size(linked_evidence) AS linked_evidence_count,
    size(assertions) AS assertion_count,
    size(complete_assertions) AS complete_assertion_count
"""

LIFECYCLE_TARGET_QUERY = """
MATCH (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
WHERE memory.memory_hash = $memory_hash
  AND memory.deleted_at IS NULL
OPTIONAL MATCH (assertion:RelationshipAssertion)-[:SUPPORTED_BY]->(evidence:Evidence)-[:FROM_MEMORY]->(memory)
WHERE evidence.memory_hash = $memory_hash
RETURN
    memory.memory_id AS memory_id,
    collect(DISTINCT elementId(evidence)) AS evidence_element_ids,
    collect(DISTINCT elementId(assertion)) AS assertion_element_ids
"""

UPDATE_MEMORY_VERSION_QUERY = """
MATCH (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
WHERE memory.memory_hash = $previous_hash
  AND memory.deleted_at IS NULL
SET
    memory.memory_hash = $memory_hash,
    memory.updated_at = $recorded_at,
    memory.deleted_at = null
RETURN memory.memory_id AS memory_id
"""

MARK_MEMORY_DELETED_QUERY = """
MATCH (memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
WHERE memory.memory_hash = $memory_hash
  AND memory.deleted_at IS NULL
SET
    memory.deleted_at = $deleted_at,
    memory.updated_at = $deleted_at
RETURN memory.memory_id AS memory_id
"""

DELETE_MEMORY_EVIDENCE_QUERY = """
UNWIND $evidence_element_ids AS evidence_element_id
MATCH (evidence:Evidence)-[:FROM_MEMORY]->(memory:Mem0Memory {
    collection_name: $collection_name,
    scope_key: $scope_key,
    memory_id: $memory_id
})
WHERE elementId(evidence) = evidence_element_id
DETACH DELETE evidence
RETURN count(*) AS evidence_deleted
"""

RETRACT_UNSUPPORTED_ASSERTIONS_QUERY = """
UNWIND $assertion_element_ids AS assertion_element_id
MATCH (assertion:RelationshipAssertion {
    collection_name: $collection_name,
    scope_key: $scope_key
})
WHERE elementId(assertion) = assertion_element_id
  AND NOT EXISTS { MATCH (assertion)-[:SUPPORTED_BY]->(:Evidence) }
SET
    assertion.state = 'RETRACTED',
    assertion.invalidated_at = $recorded_at,
    assertion.updated_at = $recorded_at
RETURN count(*) AS assertions_retracted
"""

_SUPPORTED_URI_SCHEMES = (
    "bolt://",
    "bolt+s://",
    "bolt+ssc://",
    "neo4j://",
    "neo4j+s://",
    "neo4j+ssc://",
)


class Neo4jGraphConfig(BaseModel):
    """Minimal connection settings needed by the first graph slice."""

    uri: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: SecretStr
    database: str = Field(default="neo4j", min_length=1)
    connection_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    query_timeout_seconds: float = Field(default=0.25, gt=0.0, le=30.0)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_URI_SCHEMES):
            schemes = ", ".join(scheme.removesuffix("://") for scheme in _SUPPORTED_URI_SCHEMES)
            raise ValueError(f"Neo4j URI must use one of these schemes: {schemes}")
        return value


class Neo4jResult(Protocol):
    def consume(self) -> Any: ...

    def single(self, *, strict: bool = False) -> Optional[Mapping[str, Any]]: ...

    def __iter__(self) -> Iterable[Mapping[str, Any]]: ...


class Neo4jTransaction(Protocol):
    def run(self, query: Any, **parameters: Any) -> Neo4jResult: ...


class _TimedTransaction:
    def __init__(self, transaction: Neo4jTransaction, query_factory: Callable[[str, float], Any], timeout: float):
        self._transaction = transaction
        self._query_factory = query_factory
        self._timeout = timeout

    def run(self, query: Any, **parameters: Any) -> Neo4jResult:
        timed_query = self._query_factory(query, self._timeout) if isinstance(query, str) else query
        return self._transaction.run(timed_query, **parameters)


class Neo4jSession(Protocol):
    def __enter__(self) -> "Neo4jSession": ...

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]: ...

    def run(self, query: str) -> Neo4jResult: ...

    def execute_write(self, transaction_function: Any, *args: Any, **kwargs: Any) -> Any: ...

    def execute_read(self, transaction_function: Any, *args: Any, **kwargs: Any) -> Any: ...


class Neo4jDriver(Protocol):
    def verify_connectivity(self) -> Any: ...

    def session(self, *, database: str) -> Neo4jSession: ...

    def close(self) -> Any: ...


class ProjectionConflictError(RuntimeError):
    """Raised when a memory ID is replayed with a different canonical hash."""


class GraphLifecycleConflictError(RuntimeError):
    """Raised when a stale or missing lifecycle target fails its hash guard."""


class Neo4jSchemaAdapter:
    """Own a Neo4j driver and apply the relationship-graph schema."""

    def __init__(
        self,
        config: Neo4jGraphConfig,
        driver: Neo4jDriver,
        *,
        query_factory: Callable[[str, float], Any] = lambda query, timeout: query,
    ):
        self.config = config
        self._driver = driver
        self._query_factory = query_factory
        self._closed = False

    @classmethod
    def connect(cls, config: Neo4jGraphConfig) -> "Neo4jSchemaAdapter":
        """Create an adapter using the optional Neo4j Python dependency."""
        try:
            from neo4j import GraphDatabase, Query
        except ImportError as error:
            raise ImportError(
                'Neo4j graph support requires the optional dependency: install mem0ai with the "graphs" extra'
            ) from error

        driver = GraphDatabase.driver(
            config.uri,
            auth=(config.username, config.password.get_secret_value()),
            connection_timeout=config.connection_timeout_seconds,
        )
        return cls(config=config, driver=driver, query_factory=lambda query, timeout: Query(query, timeout=timeout))

    def bootstrap_schema(self) -> int:
        """Apply every idempotent schema statement and return the count applied."""
        if self._closed:
            raise RuntimeError("cannot bootstrap schema with a closed Neo4j adapter")

        self._driver.verify_connectivity()
        with self._driver.session(database=self.config.database) as session:
            for statement in NEO4J_SCHEMA_STATEMENTS:
                query = self._query_factory(statement.strip(), self.config.query_timeout_seconds)
                session.run(query).consume()
        return len(NEO4J_SCHEMA_STATEMENTS)

    def project_relationship(
        self,
        relationship: RelationshipCandidate,
        source: ProjectionSource,
    ) -> ProjectionResult:
        """Atomically project one validated relationship and its evidence."""
        return self.project_relationships([relationship], source)[0]

    def project_relationships(
        self,
        relationships: Iterable[RelationshipCandidate],
        source: ProjectionSource,
    ) -> list[ProjectionResult]:
        """Atomically project a fully validated relationship batch."""
        if self._closed:
            raise RuntimeError("cannot project a relationship with a closed Neo4j adapter")

        relationship_batch = list(relationships)
        if not relationship_batch:
            return []
        parameter_batch = [_projection_parameters(relationship, source) for relationship in relationship_batch]
        with self._driver.session(database=self.config.database) as session:
            records = session.execute_write(self._timed_callback(self._project_relationships), parameter_batch)
        return [ProjectionResult.model_validate(dict(record)) for record in records]

    def replace_relationships(
        self,
        relationships: list[RelationshipCandidate],
        source: ProjectionSource,
        *,
        previous_hash: str,
    ) -> GraphUpdateMutation:
        """Atomically replace one memory version's evidence after validation."""
        if self._closed:
            raise RuntimeError("cannot update graph memory with a closed Neo4j adapter")
        normalized_previous_hash = _require_non_empty(previous_hash, "previous_hash")
        if normalized_previous_hash == source.memory_hash:
            raise ValueError("source memory_hash must differ from previous_hash")
        parameters = {
            **_provenance_scope_parameters(source.collection_name, source.scope),
            "memory_id": source.memory_id,
            "previous_hash": normalized_previous_hash,
            "memory_hash": source.memory_hash,
            "recorded_at": _isoformat(source.recorded_at),
        }
        parameter_batch = [_projection_parameters(relationship, source) for relationship in relationships]
        with self._driver.session(database=self.config.database) as session:
            result = session.execute_write(
                self._timed_callback(self._replace_relationships), parameters, parameter_batch
            )
        return GraphUpdateMutation.model_validate(result)

    def delete_memory(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
        memory_hash: str,
        deleted_at: datetime,
    ) -> GraphLifecycleMutation:
        """Hard-delete one memory's evidence and tombstone its graph reference."""
        if self._closed:
            raise RuntimeError("cannot delete graph memory with a closed Neo4j adapter")
        if deleted_at.tzinfo is None or deleted_at.utcoffset() is None:
            raise ValueError("deleted_at must include a timezone")
        parameters = {
            **_provenance_scope_parameters(collection_name, scope),
            "memory_id": _require_non_empty(memory_id, "memory_id"),
            "memory_hash": _require_non_empty(memory_hash, "memory_hash"),
            "deleted_at": _isoformat(deleted_at),
        }
        with self._driver.session(database=self.config.database) as session:
            result = session.execute_write(self._timed_callback(self._delete_memory), parameters)
        return GraphLifecycleMutation.model_validate(result)

    def apply(self, event: ProjectionEvent) -> ProjectionEventApplication:
        """Apply one outbox event and its graph ledger record in one transaction."""
        if self._closed:
            raise RuntimeError("cannot apply a projection event with a closed Neo4j adapter")
        payload = event.payload
        if event.intent.operation is ProjectionEventOperation.DELETE:
            if payload not in (None, ProjectionEventPayload()):
                raise ValueError("DELETE projection events require an empty payload")
        elif payload is None or not all((payload.excerpt_hash, payload.extractor_name, payload.extractor_version)):
            raise ValueError("UPSERT and UPDATE projection events require extraction provenance")

        with self._driver.session(database=self.config.database) as session:
            result = session.execute_write(self._timed_callback(self._apply_projection_event), event)
        return ProjectionEventApplication.model_validate(result)

    @classmethod
    def _apply_projection_event(
        cls,
        transaction: Neo4jTransaction,
        event: ProjectionEvent,
    ) -> Mapping[str, Any]:
        ledger_parameters = _projection_event_parameters(event)
        existing = transaction.run(READ_PROJECTION_EVENT_QUERY.strip(), event_id=event.intent.event_id).single()
        if existing is not None:
            if not _same_projection_event(existing, ledger_parameters):
                raise ProjectionConflictError("projection event ID already identifies a different graph mutation")
            return {
                "event_id": event.intent.event_id,
                "operation": event.intent.operation.value,
                "already_applied": True,
            }

        intent = event.intent
        payload = event.payload
        if intent.operation is ProjectionEventOperation.DELETE:
            parameters = {
                **_provenance_scope_parameters(intent.collection_name, intent.scope),
                "memory_id": intent.memory_id,
                "memory_hash": intent.memory_hash,
                "deleted_at": _isoformat(intent.occurred_at),
            }
            cls._delete_memory(transaction, parameters)
        else:
            if payload is None or payload.excerpt_hash is None:
                raise ValueError("projection event extraction provenance is missing")
            source = ProjectionSource(
                collection_name=intent.collection_name,
                scope=intent.scope,
                memory_id=intent.memory_id,
                memory_hash=intent.memory_hash,
                excerpt_hash=payload.excerpt_hash,
                source_kind=intent.source_kind,
                projection_method=ProjectionMethod.LIVE,
                extractor_name=payload.extractor_name,
                extractor_version=payload.extractor_version,
                model_id=payload.model_id,
                recorded_at=intent.occurred_at,
            )
            parameter_batch = [_projection_parameters(relationship, source) for relationship in payload.relationships]
            if intent.operation is ProjectionEventOperation.UPDATE:
                parameters = {
                    **_provenance_scope_parameters(intent.collection_name, intent.scope),
                    "memory_id": intent.memory_id,
                    "previous_hash": intent.previous_hash,
                    "memory_hash": intent.memory_hash,
                    "recorded_at": _isoformat(intent.occurred_at),
                }
                cls._replace_relationships(transaction, parameters, parameter_batch)
            else:
                cls._project_relationships(transaction, parameter_batch)

        recorded = transaction.run(RECORD_PROJECTION_EVENT_QUERY.strip(), **ledger_parameters).single()
        if recorded is None:
            raise RuntimeError("Neo4j did not record the applied projection event")
        return {
            "event_id": event.intent.event_id,
            "operation": event.intent.operation.value,
            "already_applied": False,
        }

    @classmethod
    def _project_relationships(
        cls,
        transaction: Neo4jTransaction,
        parameter_batch: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return [cls._project_relationship(transaction, parameters) for parameters in parameter_batch]

    @classmethod
    def _replace_relationships(
        cls,
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
        parameter_batch: list[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        target = cls._lifecycle_target(
            transaction,
            {**parameters, "memory_hash": parameters["previous_hash"]},
        )
        updated = transaction.run(UPDATE_MEMORY_VERSION_QUERY.strip(), **parameters).single()
        if updated is None:
            raise GraphLifecycleConflictError("graph memory update target changed during its transaction")
        projections = [cls._project_relationship(transaction, item) for item in parameter_batch]
        evidence_deleted = cls._delete_evidence(transaction, parameters, target["evidence_element_ids"])
        assertions_retracted = cls._retract_assertions(
            transaction,
            parameters,
            target["assertion_element_ids"],
            parameters["recorded_at"],
        )
        return {
            "memory_id": parameters["memory_id"],
            "evidence_deleted": evidence_deleted,
            "assertions_retracted": assertions_retracted,
            "projections": projections,
        }

    @classmethod
    def _delete_memory(
        cls,
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        target = cls._lifecycle_target(transaction, parameters)
        deleted = transaction.run(MARK_MEMORY_DELETED_QUERY.strip(), **parameters).single()
        if deleted is None:
            raise GraphLifecycleConflictError("graph memory delete target changed during its transaction")
        evidence_deleted = cls._delete_evidence(transaction, parameters, target["evidence_element_ids"])
        assertions_retracted = cls._retract_assertions(
            transaction,
            parameters,
            target["assertion_element_ids"],
            parameters["deleted_at"],
        )
        return {
            "memory_id": parameters["memory_id"],
            "evidence_deleted": evidence_deleted,
            "assertions_retracted": assertions_retracted,
        }

    @staticmethod
    def _lifecycle_target(
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        record = transaction.run(LIFECYCLE_TARGET_QUERY.strip(), **parameters).single()
        if record is None:
            raise GraphLifecycleConflictError(
                "graph memory does not exist at the expected collection, scope, ID, and hash"
            )
        return record

    @staticmethod
    def _delete_evidence(
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
        evidence_element_ids: list[str],
    ) -> int:
        if not evidence_element_ids:
            return 0
        record = transaction.run(
            DELETE_MEMORY_EVIDENCE_QUERY.strip(),
            **parameters,
            evidence_element_ids=evidence_element_ids,
        ).single()
        return 0 if record is None else int(record["evidence_deleted"])

    @staticmethod
    def _retract_assertions(
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
        assertion_element_ids: list[str],
        recorded_at: str,
    ) -> int:
        if not assertion_element_ids:
            return 0
        record = transaction.run(
            RETRACT_UNSUPPORTED_ASSERTIONS_QUERY.strip(),
            **{
                **parameters,
                "assertion_element_ids": assertion_element_ids,
                "recorded_at": recorded_at,
            },
        ).single()
        return 0 if record is None else int(record["assertions_retracted"])

    def provenance_by_assertion(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        assertion: UUID,
    ) -> list[RelationshipProvenance]:
        """Return every evidence record for one assertion in an exact scope."""
        return self._read_provenance(
            READ_PROVENANCE_BY_ASSERTION_QUERY,
            {
                **_provenance_scope_parameters(collection_name, scope),
                "assertion_id": str(UUID(str(assertion))),
            },
        )

    def provenance_by_memory(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
    ) -> list[RelationshipProvenance]:
        """Return every assertion/evidence record sourced from one memory."""
        normalized_memory_id = memory_id.strip()
        if not normalized_memory_id:
            raise ValueError("memory_id must not be empty")
        return self._read_provenance(
            READ_PROVENANCE_BY_MEMORY_QUERY,
            {
                **_provenance_scope_parameters(collection_name, scope),
                "memory_id": normalized_memory_id,
            },
        )

    def candidate_signals(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        query_entities: list[EntityReference],
        candidate_memory_ids: list[str],
        explanation_limit: int,
    ) -> list[GraphCandidateSignal]:
        """Return one-hop ACTIVE assertion signals for existing semantic candidates."""
        if self._closed:
            raise RuntimeError("cannot read candidate signals with a closed Neo4j adapter")
        if not query_entities or not candidate_memory_ids:
            return []
        if explanation_limit < 1:
            raise ValueError("explanation_limit must be positive")
        parameters = {
            **_provenance_scope_parameters(collection_name, scope),
            "query_entities": [entity.identity_values() for entity in query_entities],
            "candidate_memory_ids": list(dict.fromkeys(candidate_memory_ids)),
            "explanation_limit": explanation_limit,
        }
        with self._driver.session(database=self.config.database) as session:
            records = session.execute_read(
                self._timed_callback(self._run_candidate_signals_query),
                READ_CANDIDATE_SIGNALS_QUERY.strip(),
                parameters,
            )

        grouped: dict[str, list[GraphSearchExplanation]] = {}
        scores: dict[str, float] = {}
        for record in records:
            memory_id = str(record["memory_id"])
            explanation = GraphSearchExplanation.model_validate(
                {
                    "assertion_id": record["assertion_id"],
                    "subject": record["subject"],
                    "predicate": record["predicate"],
                    "predicate_display": record["predicate_display"],
                    "object": record["object"],
                    "confidence": record["confidence"],
                }
            )
            explanations = grouped.setdefault(memory_id, [])
            if len(explanations) < explanation_limit:
                explanations.append(explanation)
            scores[memory_id] = max(scores.get(memory_id, 0.0), explanation.confidence)
        return [
            GraphCandidateSignal(
                memory_id=memory_id,
                graph_score=scores[memory_id],
                explanations=tuple(explanations),
            )
            for memory_id, explanations in grouped.items()
        ]

    def inspect(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        memory_id: str,
        memory_hash: str,
    ) -> GraphMemoryState:
        """Classify one exact canonical memory projection without mutating it."""
        parameters = {
            **_provenance_scope_parameters(collection_name, scope),
            "memory_id": _require_non_empty(memory_id, "memory_id"),
        }
        expected_hash = _require_non_empty(memory_hash, "memory_hash")
        if self._closed:
            raise RuntimeError("cannot inspect memory with a closed Neo4j adapter")

        with self._driver.session(database=self.config.database) as session:
            record = session.execute_read(self._timed_callback(self._run_inspection_query), parameters)

        memory_count = int(record["memory_count"])
        if memory_count == 0:
            return GraphMemoryState.MISSING
        if memory_count != 1 or not record["memory_hash"]:
            return GraphMemoryState.INCOMPLETE
        if record["memory_hash"] != expected_hash:
            return GraphMemoryState.CONFLICT

        evidence_count = int(record["evidence_count"])
        linked_evidence_count = int(record["linked_evidence_count"])
        assertion_count = int(record["assertion_count"])
        complete_assertion_count = int(record["complete_assertion_count"])
        if (
            evidence_count > 0
            and linked_evidence_count == evidence_count
            and assertion_count > 0
            and complete_assertion_count == assertion_count
        ):
            return GraphMemoryState.CURRENT
        return GraphMemoryState.INCOMPLETE

    def _read_provenance(
        self,
        query: str,
        parameters: Mapping[str, Any],
    ) -> list[RelationshipProvenance]:
        if self._closed:
            raise RuntimeError("cannot read provenance with a closed Neo4j adapter")

        with self._driver.session(database=self.config.database) as session:
            records = session.execute_read(self._timed_callback(self._run_provenance_query), query, parameters)
        return [RelationshipProvenance.model_validate(dict(record)) for record in records]

    @staticmethod
    def _run_provenance_query(
        transaction: Neo4jTransaction,
        query: str,
        parameters: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        return list(transaction.run(query.strip(), **parameters))

    @staticmethod
    def _run_candidate_signals_query(
        transaction: Neo4jTransaction,
        query: Any,
        parameters: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        return list(transaction.run(query, **parameters))

    def _timed_callback(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        def execute(transaction: Neo4jTransaction, *args: Any) -> Any:
            timed = _TimedTransaction(transaction, self._query_factory, self.config.query_timeout_seconds)
            return callback(timed, *args)

        return execute

    def reset_collection(self, collection_name: str) -> int:
        """Delete only Mem0 graph data belonging to one configured collection."""
        if self._closed:
            raise RuntimeError("cannot reset a collection with a closed Neo4j adapter")
        normalized = _require_non_empty(collection_name, "collection_name")
        with self._driver.session(database=self.config.database) as session:
            return int(session.execute_write(self._timed_callback(self._reset_collection), normalized))

    @staticmethod
    def _reset_collection(transaction: Neo4jTransaction, collection_name: str) -> int:
        evidence = transaction.run(DELETE_COLLECTION_EVIDENCE_QUERY.strip(), collection_name=collection_name).single()
        nodes = transaction.run(DELETE_COLLECTION_NODES_QUERY.strip(), collection_name=collection_name).single()
        return int(evidence["evidence_deleted"] if evidence else 0) + int(nodes["nodes_deleted"] if nodes else 0)

    @staticmethod
    def _run_inspection_query(
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        record = transaction.run(INSPECT_MEMORY_QUERY.strip(), **parameters).single()
        if record is None:
            raise RuntimeError("Neo4j memory inspection returned no classification record")
        return record

    @staticmethod
    def _project_relationship(
        transaction: Neo4jTransaction,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        record = transaction.run(PROJECT_RELATIONSHIP_QUERY.strip(), **parameters).single()
        if record is None:
            raise ProjectionConflictError(
                "canonical memory already exists in this collection and scope with a different memory_hash"
            )
        return record

    def close(self) -> None:
        if not self._closed:
            self._driver.close()
            self._closed = True

    def __enter__(self) -> "Neo4jSchemaAdapter":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _projection_parameters(
    relationship: RelationshipCandidate,
    source: ProjectionSource,
) -> dict[str, Any]:
    subject_id = entity_id(source.collection_name, source.scope, relationship.subject)
    object_id = entity_id(source.collection_name, source.scope, relationship.object)
    projected_assertion_id = assertion_id(source.collection_name, source.scope, relationship)
    projected_evidence_id = evidence_id(projected_assertion_id, source)

    return {
        "collection_name": source.collection_name,
        "scope_key": source.scope.key,
        "memory_id": source.memory_id,
        "memory_hash": source.memory_hash,
        "subject_entity_id": str(subject_id),
        "subject_normalized_name": relationship.subject.normalized_name,
        "subject_display_name": relationship.subject.text,
        "subject_semantic_type": relationship.subject.semantic_type,
        "object_entity_id": str(object_id),
        "object_normalized_name": relationship.object.normalized_name,
        "object_display_name": relationship.object.text,
        "object_semantic_type": relationship.object.semantic_type,
        "assertion_id": str(projected_assertion_id),
        "dedupe_key": assertion_dedupe_key(source.collection_name, source.scope, relationship),
        "predicate": relationship.predicate,
        "predicate_display": relationship.predicate_display or relationship.predicate,
        "valid_from": _isoformat(relationship.valid_from),
        "valid_to": _isoformat(relationship.valid_to),
        "evidence_id": str(projected_evidence_id),
        "excerpt_hash": source.excerpt_hash,
        "confidence": relationship.confidence,
        "observed_at": _isoformat(relationship.observed_at),
        "recorded_at": _isoformat(source.recorded_at),
        "source_kind": source.source_kind.value,
        "projection_method": source.projection_method.value,
        "extractor_name": source.extractor_name,
        "extractor_version": source.extractor_version,
        "model_id": source.model_id,
    }


def _provenance_scope_parameters(collection_name: str, scope: GraphScope) -> dict[str, str]:
    normalized_collection = _require_non_empty(collection_name, "collection_name")
    return {"collection_name": normalized_collection, "scope_key": scope.key}


def _projection_event_parameters(event: ProjectionEvent) -> dict[str, Any]:
    intent = event.intent
    canonical_payload = json.dumps(
        None if event.payload is None else event.payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "event_id": intent.event_id,
        "operation": intent.operation.value,
        "collection_name": intent.collection_name,
        "scope_key": intent.scope.key,
        "memory_id": intent.memory_id,
        "memory_hash": intent.memory_hash,
        "previous_hash": intent.previous_hash,
        "source_kind": intent.source_kind.value,
        "occurred_at": _isoformat(intent.occurred_at),
        "payload_hash": hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        "applied_at": _isoformat(datetime.now(timezone.utc)),
    }


def _same_projection_event(existing: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    immutable_fields = (
        "event_id",
        "operation",
        "collection_name",
        "scope_key",
        "memory_id",
        "memory_hash",
        "previous_hash",
        "source_kind",
        "occurred_at",
        "payload_hash",
    )
    return all(existing[field] == parameters[field] for field in immutable_fields)


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized
