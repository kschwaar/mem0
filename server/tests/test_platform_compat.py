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


def make_client(monkeypatch):
    from auth import verify_auth
    from routers import platform_compat

    fake = FakeMemory()
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


def test_v1_aliases_get_update_delete(monkeypatch):
    client, fake = make_client(monkeypatch)

    assert client.get("/v1/memories/mem-1/?source=CLI").json()["id"] == "mem-1"
    assert client.put("/v1/memories/mem-1/", json={"text": "updated"}).json()["memory"] == "updated"
    assert client.delete("/v1/memories/mem-1/?source=CLI").status_code == 200
    assert fake.updated[0] == {"memory_id": "mem-1", "text": "updated"}
    assert fake.deleted == ["mem-1"]


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

    def fake_resolve(key, db):
        seen["key"] = key
        return SimpleNamespace(email="local@example.com", role="admin")

    request = SimpleNamespace(state=SimpleNamespace(), headers={"authorization": "Token m0sk_local"})
    credentials = SimpleNamespace(scheme="Token", credentials="m0sk_local")
    monkeypatch.setattr(auth, "_resolve_user_from_api_key", fake_resolve)

    user = asyncio.run(auth.verify_auth(request, credentials=credentials, db=object()))

    assert user.email == "local@example.com"
    assert seen["key"] == "m0sk_local"
    assert request.state.auth_type == "api_key"
