from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mem0.graphs.retrieval import (
    GraphCandidateSignal,
    GraphSearchExplanation,
    RelationshipGraphSearch,
)
from mem0.memory.main import AsyncMemory, Memory


def explanation():
    return GraphSearchExplanation(
        assertion_id="33333333-3333-5333-8333-333333333333",
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
        confidence=0.9,
    )


class SignalAdapter:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def candidate_signals(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return [
            GraphCandidateSignal(
                memory_id="memory-2",
                graph_score=0.9,
                explanations=(explanation(),),
            )
        ]


def vector_memory(memory_id, text, score=0.8):
    return SimpleNamespace(
        id=memory_id,
        score=score,
        payload={
            "data": text,
            "hash": f"hash-{memory_id}",
            "user_id": "user-1",
            "created_at": "2026-08-13T00:00:00Z",
            "updated_at": "2026-08-13T00:00:00Z",
        },
    )


def sync_memory(graph_search):
    memory = Memory.__new__(Memory)
    memory.collection_name = "memories"
    memory._relationship_graph_search = graph_search
    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.return_value = [0.1]
    memory.vector_store = MagicMock()
    memory.vector_store.search.return_value = [
        vector_memory("memory-1", "Alice likes tea"),
        vector_memory("memory-2", "Alice works at Acme"),
    ]
    memory.vector_store.keyword_search.return_value = None
    memory._compute_entity_boosts = MagicMock(return_value={})
    return memory


def test_sync_search_reranks_only_semantic_candidates_and_explains(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda text: [("PERSON", "Alice")])
    adapter = SignalAdapter()
    memory = sync_memory(RelationshipGraphSearch(adapter=adapter, graph_weight=0.15))

    results = memory._search_vector_store(
        "Where does Alice work?",
        {"user_id": "user-1"},
        limit=2,
        explain=True,
    )

    assert [result["id"] for result in results] == ["memory-2", "memory-1"]
    assert results[0]["score_details"]["graph_score"] == 0.9
    assert results[0]["score_details"]["graph_boost"] == pytest.approx(0.135)
    assert results[0]["graph_explanations"][0]["predicate"] == "works_at"
    assert "graph_explanations" not in results[1]
    assert adapter.calls[0]["candidate_memory_ids"] == ["memory-1", "memory-2"]


def test_graph_failure_returns_baseline_without_sensitive_error(monkeypatch, caplog):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda text: [("PERSON", "Alice")])
    adapter = SignalAdapter(error=RuntimeError("private graph credentials"))
    memory = sync_memory(RelationshipGraphSearch(adapter=adapter))

    results = memory._search_vector_store(
        "Where does Alice work?",
        {"user_id": "user-1"},
        limit=2,
        explain=True,
    )

    assert [result["id"] for result in results] == ["memory-1", "memory-2"]
    assert all("graph_score" not in result["score_details"] for result in results)
    assert "private graph credentials" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_graph_search_is_default_off(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda text: [("PERSON", "Alice")])
    memory = sync_memory(None)

    results = memory._search_vector_store(
        "Where does Alice work?",
        {"user_id": "user-1"},
        limit=2,
        explain=True,
    )

    assert [result["id"] for result in results] == ["memory-1", "memory-2"]
    assert all("graph_score" not in result["score_details"] for result in results)


@pytest.mark.asyncio
async def test_async_search_uses_same_graph_reranking_contract(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda text: [("PERSON", "Alice")])
    adapter = SignalAdapter()
    memory = AsyncMemory.__new__(AsyncMemory)
    memory.collection_name = "memories"
    memory._relationship_graph_search = RelationshipGraphSearch(adapter=adapter, graph_weight=0.15)
    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.return_value = [0.1]
    memory.vector_store = MagicMock()
    memory.vector_store.search.return_value = [
        vector_memory("memory-1", "Alice likes tea"),
        vector_memory("memory-2", "Alice works at Acme"),
    ]
    memory.vector_store.keyword_search.return_value = None
    memory._compute_entity_boosts_async = AsyncMock(return_value={})

    results = await memory._search_vector_store(
        "Where does Alice work?",
        {"user_id": "user-1"},
        limit=2,
        explain=True,
    )

    assert [result["id"] for result in results] == ["memory-2", "memory-1"]
    assert results[0]["graph_explanations"][0]["object"]["display_name"] == "Acme"
