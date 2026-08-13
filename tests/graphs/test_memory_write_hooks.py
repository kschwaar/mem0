import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mem0.memory.main import AsyncMemory, Memory


class RecordingHook:
    def __init__(self, order, *, fail_prepare=False, fail_publish=False):
        self.order = order
        self.fail_prepare = fail_prepare
        self.fail_publish = fail_publish
        self.calls = []

    def prepare_add(self, **kwargs):
        return self._prepare("ADD", kwargs)

    def prepare_update(self, **kwargs):
        return self._prepare("UPDATE", kwargs)

    def prepare_delete(self, **kwargs):
        return self._prepare("DELETE", kwargs)

    def _prepare(self, operation, kwargs):
        self.order.append(f"prepare-{operation.lower()}")
        self.calls.append((operation, kwargs))
        if self.fail_prepare:
            raise RuntimeError("private prepare failure")
        return f"event-{operation.lower()}"

    def publish(self, event_id, *, memory_text=None):
        self.order.append(f"publish-{event_id}")
        self.calls.append(("PUBLISH", {"event_id": event_id, "memory_text": memory_text}))
        if self.fail_publish:
            raise RuntimeError("private publish failure")


def memory_for_private_writes(hook, order):
    memory = Memory.__new__(Memory)
    memory._graph_write_hook = hook
    memory.collection_name = "memories"
    memory.embedding_model = MagicMock()
    memory.vector_store = MagicMock()
    memory.vector_store.insert.side_effect = lambda **kwargs: order.append("vector-insert")
    memory.vector_store.update.side_effect = lambda **kwargs: order.append("vector-update")
    memory.vector_store.delete.side_effect = lambda **kwargs: order.append("vector-delete")
    memory.db = MagicMock()
    memory.db.add_history.side_effect = lambda *args, **kwargs: order.append("history")
    memory._remove_memory_from_entity_store = MagicMock(side_effect=lambda *args: order.append("entity-remove"))
    memory._link_entities_for_memory = MagicMock(side_effect=lambda *args: order.append("entity-link"))
    return memory


def async_memory_for_private_writes(hook, order):
    memory = AsyncMemory.__new__(AsyncMemory)
    memory._graph_write_hook = hook
    memory.collection_name = "memories"
    memory.embedding_model = MagicMock()
    memory.vector_store = MagicMock()
    memory.vector_store.insert.side_effect = lambda **kwargs: order.append("vector-insert")
    memory.vector_store.update.side_effect = lambda **kwargs: order.append("vector-update")
    memory.vector_store.delete.side_effect = lambda **kwargs: order.append("vector-delete")
    memory.db = MagicMock()
    memory.db.add_history.side_effect = lambda *args, **kwargs: order.append("history")
    memory._remove_memory_from_entity_store = AsyncMock(side_effect=lambda *args: order.append("entity-remove"))
    memory._link_entities_for_memory = AsyncMock(side_effect=lambda *args: order.append("entity-link"))
    return memory


def stored_memory(text="Alice works at Acme", memory_hash=None):
    return SimpleNamespace(
        id="memory-1",
        payload={
            "data": text,
            "hash": memory_hash or hashlib.md5(text.encode()).hexdigest(),
            "created_at": "2026-08-13T00:00:00Z",
            "updated_at": "2026-08-13T00:00:00Z",
            "user_id": "user-1",
            "role": "user",
        },
    )


def test_sync_create_prepares_before_vector_write_and_publishes_after_history(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order)
    memory = memory_for_private_writes(hook, order)

    memory_id = memory._create_memory(
        "Alice works at Acme",
        {"Alice works at Acme": [0.1]},
        {"user_id": "user-1", "role": "user"},
    )

    assert order == ["prepare-add", "vector-insert", "history", "publish-event-add"]
    operation, values = hook.calls[0]
    assert operation == "ADD"
    assert values["memory_id"] == memory_id
    assert values["memory_hash"] == hashlib.md5(b"Alice works at Acme").hexdigest()
    assert hook.calls[-1][1]["memory_text"] == "Alice works at Acme"


def test_sync_update_and_delete_use_prior_canonical_hash(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order)
    memory = memory_for_private_writes(hook, order)
    original = stored_memory()
    memory.vector_store.get.return_value = original

    memory._update_memory("memory-1", "Alice works at Beta", {"Alice works at Beta": [0.2]})
    memory._delete_memory("memory-1", original)

    update = next(values for operation, values in hook.calls if operation == "UPDATE")
    deletion = next(values for operation, values in hook.calls if operation == "DELETE")
    assert update["previous_hash"] == original.payload["hash"]
    assert update["memory_hash"] == hashlib.md5(b"Alice works at Beta").hexdigest()
    assert deletion["memory_hash"] == original.payload["hash"]
    assert order.index("prepare-update") < order.index("vector-update") < order.index("publish-event-update")
    assert order.index("prepare-delete") < order.index("vector-delete") < order.index("publish-event-delete")


def test_metadata_only_update_emits_no_graph_event(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order)
    memory = memory_for_private_writes(hook, order)
    memory.vector_store.get.return_value = stored_memory()

    memory._update_memory("memory-1", None, {}, {"topic": "employment"})

    assert all(operation not in {"UPDATE", "PUBLISH"} for operation, _ in hook.calls)
    assert "vector-update" in order


@pytest.mark.parametrize("failure", ["prepare", "publish"])
def test_graph_hook_failures_do_not_fail_canonical_sync_create(monkeypatch, caplog, failure):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order, fail_prepare=failure == "prepare", fail_publish=failure == "publish")
    memory = memory_for_private_writes(hook, order)

    memory_id = memory._create_memory("private text", {"private text": [0.1]}, {"user_id": "user-1"})

    assert memory_id
    assert "vector-insert" in order
    assert "history" in order
    assert "private" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("failure", ["vector", "history"])
def test_canonical_create_failure_never_publishes_prepared_event(monkeypatch, failure):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order)
    memory = memory_for_private_writes(hook, order)
    if failure == "vector":
        memory.vector_store.insert.side_effect = RuntimeError("vector unavailable")
    else:
        memory.db.add_history.side_effect = RuntimeError("history unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        memory._create_memory("private text", {"private text": [0.1]}, {"user_id": "user-1"})

    assert hook.calls[0][0] == "ADD"
    assert all(operation != "PUBLISH" for operation, _ in hook.calls)


@pytest.mark.asyncio
async def test_async_create_update_delete_preserve_two_phase_ordering(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    order = []
    hook = RecordingHook(order)
    memory = async_memory_for_private_writes(hook, order)
    original = stored_memory()
    memory.vector_store.get.return_value = original

    await memory._create_memory("Alice works at Acme", {"Alice works at Acme": [0.1]}, original.payload)
    await memory._update_memory("memory-1", "Alice works at Beta", {"Alice works at Beta": [0.2]})
    await memory._delete_memory("memory-1", original)

    assert order.index("prepare-add") < order.index("vector-insert") < order.index("publish-event-add")
    assert order.index("prepare-update") < order.index("vector-update") < order.index("publish-event-update")
    assert order.index("prepare-delete") < order.index("vector-delete") < order.index("publish-event-delete")


def test_default_off_mixin_does_nothing():
    memory = Memory.__new__(Memory)
    memory._graph_write_hook = None
    memory.collection_name = "memories"

    assert memory._prepare_graph_add("memory-1", "hash-1", {"user_id": "user-1"}) is None
    assert memory._prepare_graph_update("memory-1", "hash-1", "hash-2", {"user_id": "user-1"}) is None
    assert memory._prepare_graph_delete("memory-1", "hash-1", {"user_id": "user-1"}) is None
    assert memory._publish_graph_event(None) is None


def test_inferred_batch_add_uses_same_two_phase_hook(monkeypatch):
    monkeypatch.setattr("mem0.memory.main.lemmatize_for_bm25", lambda text: text)
    monkeypatch.setattr("mem0.memory.main.extract_entities_batch", lambda texts: [[] for _ in texts])
    monkeypatch.setattr("mem0.memory.main.capture_event", lambda *args, **kwargs: None)
    order = []
    hook = RecordingHook(order)
    memory = memory_for_private_writes(hook, order)
    memory.api_version = "v1.1"
    memory.custom_instructions = None
    memory.llm = MagicMock()
    memory.llm.generate_response.return_value = json.dumps({"memory": [{"text": "Alice works at Acme"}]})
    memory.embedding_model.embed.return_value = [0.1]
    memory.embedding_model.embed_batch.return_value = [[0.2]]
    memory.vector_store.search.return_value = []
    memory.db.get_last_messages.return_value = []
    memory.db.batch_add_history.side_effect = lambda records: order.append("history")

    result = memory._add_to_vector_store(
        [{"role": "user", "content": "Alice joined Acme"}],
        {"user_id": "user-1"},
        {"user_id": "user-1"},
        True,
    )

    assert len(result) == 1
    assert order == ["prepare-add", "vector-insert", "history", "publish-event-add"]
