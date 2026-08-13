from uuid import UUID

import pytest

from mem0.graphs.retrieval import (
    GraphCandidateSignal,
    GraphSearchExplanation,
    RelationshipGraphSearch,
)


def explanation(confidence=0.9):
    return GraphSearchExplanation(
        assertion_id=UUID("33333333-3333-5333-8333-333333333333"),
        subject={
            "entity_id": "11111111-1111-5111-8111-111111111111",
            "normalized_name": "alice",
            "display_name": "Alice",
            "semantic_type": "PERSON",
        },
        predicate="works_at",
        predicate_display="works at",
        object={
            "entity_id": "22222222-2222-5222-8222-222222222222",
            "normalized_name": "acme",
            "display_name": "Acme",
            "semantic_type": "ORG",
        },
        confidence=confidence,
    )


class FakeAdapter:
    def __init__(self, signals):
        self.returned_signals = signals
        self.calls = []

    def candidate_signals(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.returned_signals)


def test_search_bounds_candidates_entities_scope_and_explanations():
    adapter = FakeAdapter(
        [
            GraphCandidateSignal(
                memory_id="memory-1",
                graph_score=0.9,
                explanations=(explanation(), explanation(0.8)),
            ),
            GraphCandidateSignal(memory_id="graph-only-memory", graph_score=1.0),
        ]
    )
    search = RelationshipGraphSearch(adapter=adapter, candidate_limit=2, explanation_limit=1)

    signals = search.signals(
        collection_name="memories",
        filters={"user_id": "user-1", "agent_id": "agent-1", "topic": "work"},
        query_entities=[("PERSON", " Alice "), ("PERSON", "Alice"), ("ORG", "Acme")],
        candidate_memory_ids=["memory-1", "memory-2", "memory-3", "memory-1"],
    )

    assert set(signals) == {"memory-1"}
    assert len(signals["memory-1"].explanations) == 1
    call = adapter.calls[0]
    assert call["candidate_memory_ids"] == ["memory-1", "memory-2"]
    assert [(entity.normalized_name, entity.semantic_type) for entity in call["query_entities"]] == [
        ("alice", "PERSON"),
        ("acme", "ORG"),
    ]
    assert call["scope"].user_id == "user-1"
    assert call["scope"].agent_id == "agent-1"
    assert call["scope"].app_id is None


def test_search_with_no_entities_or_candidates_never_calls_adapter():
    adapter = FakeAdapter([])
    search = RelationshipGraphSearch(adapter=adapter)

    assert (
        search.signals(
            collection_name="memories",
            filters={"user_id": "user-1"},
            query_entities=[],
            candidate_memory_ids=["memory-1"],
        )
        == {}
    )
    assert (
        search.signals(
            collection_name="memories",
            filters={"user_id": "user-1"},
            query_entities=[("PERSON", "Alice")],
            candidate_memory_ids=[],
        )
        == {}
    )
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"graph_weight": -0.1}, "graph_weight"),
        ({"graph_weight": 1.1}, "graph_weight"),
        ({"candidate_limit": 0}, "candidate_limit"),
        ({"explanation_limit": 0}, "explanation_limit"),
    ],
)
def test_search_configuration_is_bounded(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RelationshipGraphSearch(adapter=FakeAdapter([]), **kwargs)
