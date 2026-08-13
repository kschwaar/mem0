"""Bounded relationship-graph signals for semantic candidate reranking."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem0.graphs.models import EntityReference, GraphScope, ProjectedEntity


class GraphSearchExplanation(BaseModel):
    """Compact active assertion explaining one candidate's graph signal."""

    assertion_id: UUID
    subject: ProjectedEntity
    predicate: str
    predicate_display: str
    object: ProjectedEntity
    confidence: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphCandidateSignal(BaseModel):
    """Bounded graph score and explanations for one semantic candidate."""

    memory_id: str = Field(min_length=1, max_length=512)
    graph_score: float = Field(ge=0.0, le=1.0)
    explanations: tuple[GraphSearchExplanation, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RelationshipGraphCandidateAdapter(Protocol):
    def candidate_signals(
        self,
        *,
        collection_name: str,
        scope: GraphScope,
        query_entities: list[EntityReference],
        candidate_memory_ids: list[str],
        explanation_limit: int,
    ) -> list[GraphCandidateSignal]: ...


class RelationshipGraphSearch:
    """Resolve graph evidence only for IDs already returned by semantic search."""

    def __init__(
        self,
        *,
        adapter: RelationshipGraphCandidateAdapter,
        graph_weight: float = 0.15,
        candidate_limit: int = 50,
        explanation_limit: int = 3,
    ):
        if not 0.0 <= graph_weight <= 1.0:
            raise ValueError("graph_weight must be between 0 and 1")
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or candidate_limit < 1:
            raise ValueError("candidate_limit must be a positive integer")
        if isinstance(explanation_limit, bool) or not isinstance(explanation_limit, int) or explanation_limit < 1:
            raise ValueError("explanation_limit must be a positive integer")
        self._adapter = adapter
        self.graph_weight = graph_weight
        self.candidate_limit = candidate_limit
        self.explanation_limit = explanation_limit

    def signals(
        self,
        *,
        collection_name: str,
        filters: Mapping[str, Any],
        query_entities: Iterable[tuple[str, str]],
        candidate_memory_ids: Iterable[str],
    ) -> dict[str, GraphCandidateSignal]:
        candidates = list(dict.fromkeys(str(memory_id) for memory_id in candidate_memory_ids if str(memory_id)))
        candidates = candidates[: self.candidate_limit]
        if not candidates:
            return {}

        entities = []
        seen_entities = set()
        for semantic_type, text in query_entities:
            entity = EntityReference(text=text, semantic_type=semantic_type)
            identity = (entity.normalized_name, entity.semantic_type)
            if identity not in seen_entities:
                seen_entities.add(identity)
                entities.append(entity)
            if len(entities) >= 8:
                break
        if not entities:
            return {}

        scope = GraphScope(
            user_id=filters.get("user_id"),
            agent_id=filters.get("agent_id"),
            app_id=filters.get("app_id"),
            run_id=filters.get("run_id"),
        )
        signals = self._adapter.candidate_signals(
            collection_name=collection_name,
            scope=scope,
            query_entities=entities,
            candidate_memory_ids=candidates,
            explanation_limit=self.explanation_limit,
        )

        allowed = set(candidates)
        bounded = {}
        for signal in signals:
            if signal.memory_id not in allowed:
                continue
            bounded[signal.memory_id] = signal.model_copy(
                update={"explanations": signal.explanations[: self.explanation_limit]}
            )
        return bounded
