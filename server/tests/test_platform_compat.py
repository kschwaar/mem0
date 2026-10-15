import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeMemory:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.get_all_calls = []
        self.deleted = []
        self.updated = []
        self.events = []

    def add(self, messages, **params):
        self.add_calls.append({"messages": messages, **params})
        return {"results": [{"id": "mem-1", "memory": "local memory", "event": "ADD"}]}

    def search(self, query, **params):
        self.search_calls.append({"query": query, **params})
        return {"results": [{"id": "mem-1", "memory": "local memory", "score": 0.9, "app_id": "proj"}]}

    def get_all(self, **params):
        self.get_all_calls.append(params)
        return {"results": [{"id": "mem-1", "memory": "local memory", "app_id": "proj"}]}

    def get(self, memory_id):
        return {"id": memory_id, "memory": "local memory", "app_id": "proj"}

    def update(self, **params):
        self.updated.append(params)
        return {"id": params["memory_id"], "memory": params.get("text", params.get("data"))}

    def history(self, memory_id):
        return {"results": [{"memory_id": memory_id}]}

    def delete(self, memory_id):
        self.deleted.append(memory_id)
        return {"message": "Memory deleted successfully!"}

    def delete_all(self, **params):
        self.deleted.append(params)
        return {"message": "All relevant memories deleted"}


def make_client(monkeypatch, memory=None):
    from auth import verify_auth
    from routers import platform_compat

    fake = memory or FakeMemory()
    app = FastAPI()
    monkeypatch.setattr(platform_compat, "mcp", None)
    monkeypatch.setattr(platform_compat, "StreamableHTTPServerTransport", None)
    app.include_router(platform_compat.router)
    app.dependency_overrides[verify_auth] = lambda: SimpleNamespace(email="local@example.com", role="admin")

    event_store = {}

    def fake_create_event(**kwargs):
        event_id = f"evt-{len(event_store) + 1}"
        event = {"id": event_id, "event_id": event_id, **kwargs}
        event_store[event_id] = event
        return event

    monkeypatch.setattr(platform_compat.server_state, "get_memory_instance", lambda: fake)
    monkeypatch.setattr(platform_compat, "create_event", fake_create_event)
    monkeypatch.setattr(platform_compat, "list_events", lambda limit=100: list(event_store.values())[:limit])
    monkeypatch.setattr(platform_compat, "get_event", lambda event_id: event_store[event_id])
    return TestClient(app), fake


def test_v2_list_pages_until_exhaustion_and_selects_all_memory_ids(monkeypatch):
    class PagedMemory(FakeMemory):
        def get_all(self, **params):
            return {
                "results": [
                    {"id": memory_id, "memory": memory_id}
                    for memory_id in ("mem-1", "mem-2", "mem-3")[:params["top_k"]]
                ]
            }

    client, _ = make_client(monkeypatch, PagedMemory())
    pages = []
    for page in (1, 2, 3):
        response = client.post(
            f"/v2/memories/?page={page}&page_size=2",
            json={"filters": {"user_id": "user-1"}},
        )
        assert response.status_code == 200
        pages.append([item["id"] for item in response.json()["results"]])
        if not response.json()["results"]:
            break

    assert pages == [["mem-1", "mem-2"], ["mem-3"], []]


def test_v2_list_promotes_app_id_without_dropping_metadata(monkeypatch):
    class MetadataMemory(FakeMemory):
        def get_all(self, **params):
            return {
                "results": [
                    {"id": "mem-app", "memory": "local", "metadata": {"app_id": "proj", "tag": "keep"}}
                ]
            }

    client, _ = make_client(monkeypatch, MetadataMemory())
    response = client.post("/v2/memories/?page_size=10", json={"filters": {"user_id": "u1"}})

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "id": "mem-app",
            "memory": "local",
            "app_id": "proj",
            "metadata": {"app_id": "proj", "tag": "keep"},
        }
    ]


def test_v2_list_paginates_beyond_thousand_results(monkeypatch):
    class LargeMemory(FakeMemory):
        def get_all(self, **params):
            return {
                "results": [
                    {"id": f"mem-{index}", "memory": f"memory {index}"}
                    for index in range(min(params["top_k"], 1001))
                ]
            }

    client, _ = make_client(monkeypatch, LargeMemory())
    response = client.post("/v2/memories/?page=11&page_size=100", json={"filters": {"user_id": "u1"}})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["results"]] == ["mem-1000"]
    exhausted = client.post("/v2/memories/?page=12&page_size=100", json={"filters": {"user_id": "u1"}})
    assert exhausted.status_code == 200
    assert exhausted.json()["results"] == []


def test_v3_add_preserves_app_id_in_metadata_and_returns_event(monkeypatch):
    client, fake = make_client(monkeypatch)

    response = client.post(
        "/v3/memories/add/",
        json={
            "messages": [{"role": "user", "content": "remember local memory"}],
            "user_id": "u1",
            "app_id": "proj",
            "source": "CLI",
            "rerank": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == "evt-1"
    assert fake.add_calls[0]["metadata"]["app_id"] == "proj"


def test_v3_search_preserves_app_id_filter(monkeypatch):
    client, fake = make_client(monkeypatch)

    response = client.post(
        "/v3/memories/search/",
        json={"query": "local", "filters": {"AND": [{"user_id": "u1"}, {"app_id": "proj"}]}},
    )

    assert response.status_code == 200
    assert fake.search_calls[0]["filters"] == {"AND": [{"user_id": "u1"}, {"app_id": "proj"}]}


def make_qdrant_store():
    from qdrant_client import QdrantClient, models

    from mem0.vector_stores.qdrant import Qdrant

    store = object.__new__(Qdrant)
    store.client = QdrantClient(":memory:")
    store.collection_name = "boundary"
    store.client.create_collection(
        collection_name=store.collection_name,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    payloads = [
        {"data": "matching", "agent_id": "proj", "app_id": "proj", "dirs": ["src", "src/components"]},
        {"data": "sibling", "agent_id": "proj", "app_id": "proj", "dirs": ["src-other"]},
        {"data": "personal", "user_id": "u1", "app_id": "proj", "dirs": ["notes"]},
        {"data": "other", "user_id": "other", "app_id": "proj", "dirs": ["src"]},
    ]
    store.client.upsert(
        collection_name=store.collection_name,
        points=[
            models.PointStruct(id=index, vector=[1.0, 0.0], payload=payload)
            for index, payload in enumerate(payloads, 1)
        ],
    )
    return store


def test_v3_search_plugin_directory_filter_matches_exact_directory_chain(monkeypatch):
    from mem0.memory.main import Memory

    store = make_qdrant_store()
    try:
        memory = object.__new__(Memory)
        memory.api_version = "v1.1"
        memory.reranker = None

        def search_vectors(query, filters, limit, threshold, **kwargs):
            return [
                {"id": str(row.id), "memory": row.payload["data"], "score": row.score}
                for row in store.search(query, [1.0, 0.0], top_k=limit, filters=filters)
            ]

        memory._search_vector_store = search_vectors
        monkeypatch.setattr("mem0.memory.main.capture_event", lambda *args, **kwargs: None)
        monkeypatch.setattr("mem0.memory.main.display_first_run_notice", lambda *args, **kwargs: None)
        client, _ = make_client(monkeypatch, memory)
        response = client.post(
            "/v3/memories/search/",
            json={
                "query": "local",
                "filters": {
                    "OR": [
                        {"AND": [
                            {"agent_id": "proj"}, {"app_id": "proj"},
                            {"metadata": {"dirs": {"contains": "src"}}},
                        ]},
                        {"AND": [{"user_id": "u1"}, {"app_id": "proj"}]},
                    ]
                },
            },
        )
        assert response.status_code == 200
        assert {item["memory"] for item in response.json()["results"]} == {"matching", "personal"}
    finally:
        store.client.close()


def test_v3_search_rejects_ambiguous_metadata_payload_collisions(monkeypatch):
    client, _ = make_client(monkeypatch)
    for filters in (
        {"user_id": "u1", "metadata": {"user_id": "other"}},
        {"metadata": {"user_id": "other"}, "user_id": "u1"},
    ):
        response = client.post("/v3/memories/search/", json={"query": "local", "filters": filters})
        assert response.status_code == 400


def test_v1_entities_reads_real_qdrant_scroll_results(monkeypatch):
    from routers import entities

    store = make_qdrant_store()
    try:
        client, _ = make_client(monkeypatch)
        monkeypatch.setattr(entities, "get_memory_instance", lambda: SimpleNamespace(vector_store=store))
        response = client.get("/v1/entities/")
        assert response.status_code == 200
        assert {
            (row["type"], row["id"]): row["total_memories"] for row in response.json()
        } == {("agent", "proj"): 2, ("app", "proj"): 4, ("user", "u1"): 1, ("user", "other"): 1}
    finally:
        store.client.close()


def test_v1_app_scoped_delete_rejects_capped_results_without_deleting(monkeypatch):
    class CappedMemory(FakeMemory):
        def get_all(self, **params):
            return {"results": [{"id": f"mem-{index}"} for index in range(1000)]}

    client, fake = make_client(monkeypatch, CappedMemory())
    response = client.delete("/v1/memories/?app_id=proj")

    assert response.status_code == 409
    assert "Narrow the scope and retry" in response.json()["detail"]
    assert fake.deleted == []


def test_v1_aliases_get_update_delete(monkeypatch):
    client, fake = make_client(monkeypatch)

    assert client.get("/v1/memories/mem-1/?source=CLI").json()["id"] == "mem-1"
    assert client.put("/v1/memories/mem-1/", json={"text": "updated"}).json()["memory"] == "updated"
    assert client.delete("/v1/memories/mem-1/?source=CLI").status_code == 200
    assert fake.updated[0] == {"memory_id": "mem-1", "text": "updated"}
    assert fake.deleted == ["mem-1"]


def test_v1_get_returns_404_for_missing_memory(monkeypatch):
    class MissingMemory(FakeMemory):
        def get(self, memory_id):
            return None

    client, _ = make_client(monkeypatch, MissingMemory())
    response = client.get("/v1/memories/missing/")

    assert response.status_code == 404


def test_mcp_initialize_tools_and_unknown_tool(monkeypatch):
    client, _fake = make_client(monkeypatch)

    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "mem0-self-hosted"

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert {"add_memory", "search_memories", "get_memories", "delete_memory"}.issubset(names)

    missing = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
    )
    assert missing.json()["error"]["message"] == "Unknown tool: nope"


def test_mcp_add_memory_accepts_text_argument(monkeypatch):
    client, fake = make_client(monkeypatch)

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "add_memory",
                "arguments": {"text": "remember this", "user_id": "u1", "app_id": "proj", "infer": False},
            },
        },
    )

    assert response.status_code == 200
    assert "result" in response.json()
    assert fake.add_calls[0]["messages"] == [{"role": "user", "content": "remember this"}]


def test_mcp_add_memory_uses_header_identity_defaults(monkeypatch):
    client, fake = make_client(monkeypatch)

    response = client.post(
        "/mcp/",
        headers={"X-User-ID": "u1", "X-Agent-ID": "agent-local", "X-App-ID": "proj"},
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "add_memory", "arguments": {"text": "remember via headers"}},
        },
    )

    assert response.status_code == 200
    assert "result" in response.json()
    assert fake.add_calls[0]["user_id"] == "u1"
    assert fake.add_calls[0]["agent_id"] == "agent-local"
    assert fake.add_calls[0]["metadata"]["app_id"] == "proj"


def test_token_auth_uses_api_key_resolver(monkeypatch):
    import auth

    seen = {}
    database = object()

    class SessionContext:
        def __enter__(self):
            return database

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    def fake_resolve(key, db):
        seen["key"] = key
        seen["db"] = db
        return SimpleNamespace(email="local@example.com", role="admin")

    request = SimpleNamespace(state=SimpleNamespace(), headers={"authorization": "Token m0sk_local"})
    credentials = SimpleNamespace(scheme="Token", credentials="m0sk_local")
    monkeypatch.setattr(auth, "_resolve_user_from_api_key", fake_resolve)
    monkeypatch.setattr(auth, "SessionLocal", SessionContext)

    user = asyncio.run(auth.verify_auth(request, credentials=credentials))

    assert user.email == "local@example.com"
    assert seen["key"] == "m0sk_local"
    assert seen["db"] is database
    assert request.state.auth_type == "api_key"
