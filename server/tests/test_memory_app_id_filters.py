from types import SimpleNamespace

import main


class FakeMemory:
    def __init__(self):
        self.get_all_calls = []
        self.search_calls = []
        self.deleted = []

    def get_all(self, **params):
        self.get_all_calls.append(params)
        return {"results": [{"id": "memory-1"}, {"id": "memory-2"}]}

    def search(self, query, **params):
        self.search_calls.append({"query": query, **params})
        return {"results": []}

    def delete(self, memory_id):
        self.deleted.append(memory_id)


def install_fake_memory(monkeypatch):
    memory = FakeMemory()
    monkeypatch.setattr(main, "get_memory_instance", lambda: memory)
    return memory


def test_list_memories_passes_app_id_as_top_level_filter(monkeypatch):
    memory = install_fake_memory(monkeypatch)

    response = main.get_all_memories(
        SimpleNamespace(state=SimpleNamespace(auth_type="admin_api_key")),
        user_id="user-1",
        app_id="app-1",
        top_k=None,
        show_expired=False,
        _auth=SimpleNamespace(role="admin"),
    )

    assert response == {"results": [{"id": "memory-1"}, {"id": "memory-2"}]}
    assert memory.get_all_calls == [
        {"filters": {"user_id": "user-1", "app_id": "app-1"}, "show_expired": False}
    ]


def test_deprecated_search_app_id_remains_a_top_level_filter(monkeypatch):
    memory = install_fake_memory(monkeypatch)

    response = main.search_memories(
        main.SearchRequest(query="local memory", user_id="user-1", app_id="app-1"),
        _auth=SimpleNamespace(role="admin"),
    )

    assert response == {"results": []}
    assert memory.search_calls == [
        {"query": "local memory", "filters": {"user_id": "user-1", "app_id": "app-1"}}
    ]


def test_delete_memories_lists_by_top_level_app_id_before_deleting(monkeypatch):
    memory = install_fake_memory(monkeypatch)

    response = main.delete_all_memories(
        user_id="user-1",
        app_id="app-1",
        _auth=SimpleNamespace(role="admin"),
    )

    assert response.message == "All relevant memories deleted"
    assert memory.get_all_calls == [
        {
            "filters": {"user_id": "user-1", "app_id": "app-1"},
            "top_k": main.ALL_MEMORIES_LIMIT,
            "show_expired": True,
        }
    ]
    assert memory.deleted == ["memory-1", "memory-2"]
