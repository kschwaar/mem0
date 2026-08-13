from types import SimpleNamespace

import pytest
from qdrant_client.models import PointStruct

from mem0.graphs.models import GraphScope, SourceKind
from mem0.graphs.readers import (
    CanonicalMemoryReadError,
    CanonicalMemoryRecordError,
    CanonicalMemorySnapshotLimitError,
    VectorStoreCanonicalMemoryReader,
)
from mem0.vector_stores.qdrant import Qdrant


def row(memory_id, *, text=None, memory_hash=None, **payload):
    return SimpleNamespace(
        id=memory_id,
        payload={
            "data": text if text is not None else f"text for {memory_id}",
            "hash": memory_hash if memory_hash is not None else f"hash-{memory_id}",
            "user_id": "user-1",
            **payload,
        },
    )


class FakeVectorStore:
    collection_name = "memories"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def reader(result, *, max_records=100):
    store = FakeVectorStore(result)
    return VectorStoreCanonicalMemoryReader(
        vector_store=store,
        collection_name="memories",
        max_records=max_records,
    ), store


def test_pages_sorted_exact_scope_memories_with_opaque_keyset_cursor():
    graph_reader, store = reader(
        [
            row("memory-c", source_kind="assistant"),
            row("memory-a", role="user"),
            row("memory-b", role="system"),
        ]
    )
    scope = GraphScope(user_id="user-1")

    first = graph_reader.page(collection_name="memories", scope=scope, cursor=None, limit=2)
    second = graph_reader.page(collection_name="memories", scope=scope, cursor=first.next_cursor, limit=2)

    assert [memory.memory_id for memory in first.memories] == ["memory-a", "memory-b"]
    assert [memory.source_kind for memory in first.memories] == [SourceKind.USER, SourceKind.SYSTEM]
    assert first.next_cursor and "memory-b" not in first.next_cursor
    assert [memory.memory_id for memory in second.memories] == ["memory-c"]
    assert second.memories[0].source_kind is SourceKind.ASSISTANT
    assert second.next_cursor is None
    assert store.calls == [
        {"filters": {"user_id": "user-1"}, "top_k": 101},
        {"filters": {"user_id": "user-1"}, "top_k": 101},
    ]


@pytest.mark.parametrize(
    "wrapped",
    [
        lambda records: [records],
        lambda records: (records, "provider-cursor"),
    ],
)
def test_normalizes_nested_and_tuple_vector_store_results(wrapped):
    graph_reader, _ = reader(wrapped([row("memory-1")]))

    page = graph_reader.page(
        collection_name="memories",
        scope=GraphScope(user_id="user-1"),
        cursor=None,
        limit=10,
    )

    assert [memory.memory_id for memory in page.memories] == ["memory-1"]


def test_passes_every_populated_scope_dimension_to_vector_store():
    scope = GraphScope(user_id="user-1", agent_id="agent-1", app_id="app-1", run_id="run-1")
    graph_reader, store = reader([row("memory-1", agent_id="agent-1", app_id="app-1", run_id="run-1")])

    graph_reader.page(collection_name="memories", scope=scope, cursor=None, limit=10)

    assert store.calls[0]["filters"] == scope.canonical_values()


def test_keyset_cursor_resumes_after_deleted_boundary_record():
    graph_reader, store = reader([row("memory-a"), row("memory-b"), row("memory-c")])
    scope = GraphScope(user_id="user-1")
    first = graph_reader.page(collection_name="memories", scope=scope, cursor=None, limit=2)
    store.result = [row("memory-a"), row("memory-c")]

    resumed = graph_reader.page(collection_name="memories", scope=scope, cursor=first.next_cursor, limit=2)

    assert [memory.memory_id for memory in resumed.memories] == ["memory-c"]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ([SimpleNamespace(id="memory-1", payload=None)], "payload"),
        ([SimpleNamespace(id=None, payload={})], "missing an ID"),
        ([row("memory-1", text=" ")], "memory text"),
        ([row("memory-1", memory_hash=" ")], "memory hash"),
        ([row("memory-1", user_id="another-user")], "requested scope"),
        ([row("memory-1", source_kind="external")], "source_kind"),
        ([row("x" * 513)], "canonical record contract"),
        ([row("memory-1"), row("memory-1")], "duplicate"),
    ],
)
def test_fails_closed_on_malformed_or_unsafe_records(result, message):
    graph_reader, _ = reader(result)

    with pytest.raises(CanonicalMemoryRecordError, match=message):
        graph_reader.page(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            cursor=None,
            limit=10,
        )


def test_fails_instead_of_silently_truncating_snapshot():
    graph_reader, _ = reader([row("memory-a"), row("memory-b"), row("memory-c")], max_records=2)

    with pytest.raises(CanonicalMemorySnapshotLimitError, match="max_records"):
        graph_reader.page(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            cursor=None,
            limit=1,
        )


@pytest.mark.parametrize("result", [{"rows": []}, "not rows", object()])
def test_rejects_unsupported_vector_store_result_shapes(result):
    graph_reader, _ = reader(result)

    with pytest.raises(CanonicalMemoryReadError, match="unsupported"):
        graph_reader.page(
            collection_name="memories",
            scope=GraphScope(user_id="user-1"),
            cursor=None,
            limit=10,
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"collection_name": "other", "cursor": None, "limit": 1}, "collection_name"),
        ({"collection_name": "memories", "cursor": "not-a-cursor", "limit": 1}, "cursor"),
        ({"collection_name": "memories", "cursor": None, "limit": 0}, "limit"),
    ],
)
def test_rejects_invalid_page_contract_before_reading(arguments, message):
    graph_reader, store = reader([])

    with pytest.raises(ValueError, match=message):
        graph_reader.page(scope=GraphScope(user_id="user-1"), **arguments)

    assert store.calls == []


def test_constructor_rejects_mismatched_provider_collection_and_invalid_bounds():
    store = FakeVectorStore([])

    with pytest.raises(ValueError, match="configured vector store"):
        VectorStoreCanonicalMemoryReader(vector_store=store, collection_name="other")
    with pytest.raises(ValueError, match="max_records"):
        VectorStoreCanonicalMemoryReader(vector_store=store, collection_name="memories", max_records=0)


def test_reads_canonical_memories_from_real_local_qdrant(tmp_path):
    store = Qdrant(
        collection_name="graph-reader-integration",
        embedding_model_dims=2,
        path=str(tmp_path / "qdrant"),
        on_disk=False,
    )
    store.client.upsert(
        collection_name=store.collection_name,
        wait=True,
        points=[
            PointStruct(
                id="22222222-2222-4222-8222-222222222222",
                vector=[0.0, 1.0],
                payload={"data": "second", "hash": "hash-2", "user_id": "user-1", "role": "assistant"},
            ),
            PointStruct(
                id="11111111-1111-4111-8111-111111111111",
                vector=[1.0, 0.0],
                payload={"data": "first", "hash": "hash-1", "user_id": "user-1", "role": "user"},
            ),
            PointStruct(
                id="33333333-3333-4333-8333-333333333333",
                vector=[1.0, 1.0],
                payload={"data": "other", "hash": "hash-3", "user_id": "other-user"},
            ),
        ],
    )
    graph_reader = VectorStoreCanonicalMemoryReader(
        vector_store=store,
        collection_name=store.collection_name,
    )

    try:
        page = graph_reader.page(
            collection_name=store.collection_name,
            scope=GraphScope(user_id="user-1"),
            cursor=None,
            limit=10,
        )
    finally:
        store.client.close()

    assert [(memory.memory_text, memory.source_kind) for memory in page.memories] == [
        ("first", SourceKind.USER),
        ("second", SourceKind.ASSISTANT),
    ]
