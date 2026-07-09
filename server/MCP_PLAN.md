# Plan: Add MCP Server to `server/`

## Status: Implemented and verified (2026-07-01)

All 9 tools are implemented in `server/routers/mcp_server.py`, wired into
`main.py`, and confirmed working end-to-end: `tools/list` returns all 9
tools, auth via `verify_auth`/`X-API-Key` works, a fresh Claude Code session
connects via the reinstalled plugin, and `add_memory`/`search_memories`
round-tripped successfully in a live session.

One bug found and fixed during verification: the Streamable HTTP response
handler passed raw `bytes` headers into Starlette's `Response(headers=...)`,
which expects `str` — fixed by decoding (`k.decode("latin-1")`), matching
`openmemory/api/app/mcp_server.py`'s reference pattern which already did
this correctly.

Not exercised during manual testing (implemented, but unverified against
real data): `list_entities` / `delete_entities`. Also not explicitly
re-confirmed: memories appearing in the dashboard at `localhost:3333`
(verification step 5, below) — deprioritized since core functionality was
already confirmed sufficient.

---

## Goal

Add a FastMCP server to the existing `server/` FastAPI app so that local
coding agents/harnesses (Claude Code, Pi, etc.) can connect via the MCP
protocol and use memory tools backed by the same `Memory` instance the REST
API uses.

The plugin at `integrations/mem0-plugin` will then be patched to point at
`http://localhost:8888/mcp/` instead of `https://mcp.mem0.ai/mcp/`.

---

## Background / Context

- `server/main.py` is a FastAPI app running at `localhost:8888`.
- Memory is accessed via `get_memory_instance()` (`server/server_state.py`).
- `server/docker-compose.yaml` already runs Qdrant (vector store) and Neo4j
  (`neo4j-mem0`, graph store) — `Memory.delete_all()` already cascades graph
  deletes internally, no graph-specific code needed in the MCP layer.
- Auth: `server/auth.py` exposes `verify_auth` as a plain async function
  used via FastAPI `Depends`. It is **not** Depends-only — it can be used
  directly as a dependency on the MCP route, no duplication/reimplementation
  needed.
- Transport: Streamable HTTP only (SSE deprecated). The reference pattern in
  `openmemory/api/app/mcp_server.py:496-565` (`handle_streamable_http`,
  `capture_send` interception of the ASGI response) is a normal FastAPI
  `@router.api_route(...)` handler — confirmed it does NOT need a `Mount`;
  copy it close to verbatim.

### Deployment context (resolved — drives the decisions below)

This is a **strictly local, single-user, single-process docker-compose
deployment**. One person, one Mem0 store, multiple personal agents/harnesses
(Claude Code, Pi, etc.) reading/writing the same memory pool. There is no
other tenant to defend against. This downgrades several concerns from
"security requirement" to "correctness/UX nice-to-have":

- **No privilege-escalation concern** for caller-supplied `X-User-ID`. There
  is only one real user; trusting the header is fine.
- **No ownership check needed** on `delete_memory`/`delete_all_memories` by
  ID — there's no other user's data to protect.
- Auth (`X-API-Key` → `verify_auth`) still needs a real, complete
  implementation (don't leave it open on the Docker bridge/LAN), but doesn't
  need to be hardened beyond "actually works."
- Single-worker assumption is fine (module-level `FastMCP`/`ContextVar`
  state). Do not add `uvicorn --workers >1` without revisiting this.

### Identity / agent_id semantics (resolved)

Multiple agents should share **one memory pool** scoped by `user_id` only.
`agent_id` is metadata, not a retrieval filter:

- `X-User-ID` header → `user_id` (falls back to `MEM0_MCP_USER_ID` env var).
- `X-Agent-ID` header → `agent_id` (falls back to `MEM0_MCP_AGENT_ID` env
  var, default `"claude-code"` — the primary agent in use).
- `add_memory` stores `agent_id` as metadata (so you can later see/query
  which agent wrote what).
- `search_memories` / `get_memories` filter by `user_id` **only** — never by
  `agent_id`. This is the key fix: `agent_id` must never be used as a
  truthy-gated filter, since the new `"claude-code"` default is always
  truthy and would otherwise silo every agent's memories from each other.

---

## Tool surface: all 9 tools, matching plugin naming exactly

Earlier drafts of this plan only covered 5 ops under different names
(`list_memories`, batch `delete_memories`). Research into
`integrations/mem0-plugin/.opencode-plugin/opencode-mem0.ts` (the working
reference for tool names) and the `forget`/`mem0-forget` skills
(`integrations/mem0-plugin/skills/forget/SKILL.md`,
`.opencode-plugin/opencode-skills/mem0-forget/SKILL.md`) confirmed the
skills call these exact 9 tool names — so the server must expose tools
under these names, not synonyms, or the bundled skills will call
nonexistent tools:

| Tool | Maps to | Notes |
|---|---|---|
| `add_memory` | `memory.add()` | stores `agent_id` as metadata only |
| `search_memories` | `memory.search()` | filter: `user_id` only |
| `get_memories` | `memory.get_all()` | filter: `user_id` only (renamed from draft's `list_memories`) |
| `get_memory` | `memory.get(id)` | new — single fetch by ID |
| `update_memory` | `memory.update(id, ...)` | new |
| `delete_memory` | `memory.delete(id)` | **singular**, matches `forget` skill's per-ID delete (renamed from draft's batch `delete_memories`) |
| `delete_all_memories` | `memory.delete_all()` | unchanged |
| `delete_entities` | `entities.py` delete logic | new — accepts optional `user_id`/`agent_id`/`run_id`, Platform-API shape (matches `opencode-mem0.ts:579-597`'s `deleteUsers` call, not `entities.py`'s `(type, id)` shape) |
| `list_entities` | `entities.py` list logic | new — lists `user`/`agent`/`run` identifiers (NOT graph nodes — confirmed via `opencode-mem0.ts:599-613`'s `mem0.users()`, which is the same identifier model as `entities.py`, just without `app_id` since this server is single-app) |

`get_entities`/`get_event_status` (Platform async-event polling) is **not**
implemented — it's specific to the hosted Platform API's async job model,
which the self-hosted `Memory` class doesn't have.

`get_memory`/`update_memory`/`list_entities`/`delete_entities` are each
small (~10-20 lines): `get_memory`/`update_memory` mirror the existing REST
handlers in `main.py:465`/`508-524`; `list_entities`/`delete_entities` reuse
the logic already in `server/routers/entities.py:43-76` (just called
directly as functions, not through their `Depends`-wrapped FastAPI routes).

---

## Step 1: Add FastMCP dependency

File: `server/requirements.txt`

Add:
```
mcp[cli]>=1.0.0
anyio>=4.0.0
```

`anyio` is already present transitively (via `starlette`/`fastapi`), but pin
it explicitly — Streamable HTTP uses `anyio.create_task_group()` directly.

**Reminder:** after editing `requirements.txt`, rebuild the image
(`docker compose -f server/docker-compose.yaml up mem0 -d --build`) — a
plain restart without `--build` reuses the stale image and the import
fails at startup.

---

## Step 2: Create `server/routers/mcp_server.py`

### 2a. Imports and initialization

```python
import contextvars
import json
import logging
import os

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport

from auth import verify_auth
from routers.entities import TYPE_TO_FIELD, _iter_payloads, _parse_timestamp
from server_state import get_memory_instance

logger = logging.getLogger(__name__)

mcp = FastMCP("mem0-mcp-server")
mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])

user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id")
agent_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id")
```

### 2b. Identity resolution helper

```python
def _resolve_identity(request: Request) -> tuple[str, str]:
    user_id = request.headers.get("X-User-ID") or os.getenv("MEM0_MCP_USER_ID")
    agent_id = request.headers.get("X-Agent-ID") or os.getenv("MEM0_MCP_AGENT_ID", "claude-code")
    if not user_id:
        raise ValueError(
            "user_id is required. Pass X-User-ID header or set MEM0_MCP_USER_ID env var."
        )
    return user_id, agent_id
```

### 2c. Auth — reuse `verify_auth` directly via `Depends`

No custom auth helper needed. `verify_auth` (`server/auth.py:144`) is a
plain async function already designed for FastAPI dependency injection —
use it the same way the REST routers do:

```python
@mcp_router.api_route("/", methods=["POST", "GET", "DELETE"])
async def handle_streamable_http(request: Request, _auth=Depends(verify_auth)):
    ...
```

`verify_auth` raises `HTTPException(401)` itself on bad/missing
credentials, and respects `AUTH_DISABLED`/`ADMIN_API_KEY` the same way every
other endpoint does — no duplicated header-parsing logic.

### 2d. The 9 MCP tools

Each tool reads identity from contextvars and calls `get_memory_instance()`.
Filtering uses `user_id` only — `agent_id` is metadata on writes, never a
read filter.

```python
@mcp.tool(description="Store a new memory.")
async def add_memory(text: str, infer: bool = True, metadata: dict | None = None) -> str:
    user_id = user_id_var.get(None)
    agent_id = agent_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    meta = dict(metadata or {})
    if agent_id:
        meta["agent_id"] = agent_id
    try:
        result = get_memory_instance().add(text, user_id=user_id, infer=infer, metadata=meta)
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error adding memory")
        return f"Error: {e}"


@mcp.tool(description="Search memories. Call this on every user message.")
async def search_memories(query: str, limit: int = 10) -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        result = get_memory_instance().search(query=query, filters={"user_id": user_id}, limit=limit)
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error searching memories")
        return f"Error: {e}"


@mcp.tool(description="List all stored memories for this user.")
async def get_memories() -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        result = get_memory_instance().get_all(filters={"user_id": user_id})
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error listing memories")
        return f"Error: {e}"


@mcp.tool(description="Retrieve a specific memory by its ID.")
async def get_memory(memory_id: str) -> str:
    try:
        return json.dumps(get_memory_instance().get(memory_id))
    except Exception as e:
        logger.exception("Error getting memory")
        return f"Error: {e}"


@mcp.tool(description="Update the content or metadata of a specific memory.")
async def update_memory(memory_id: str, text: str | None = None, metadata: dict | None = None) -> str:
    try:
        params = {"memory_id": memory_id}
        if text is not None:
            params["data"] = text
        if metadata is not None:
            params["metadata"] = metadata
        return json.dumps(get_memory_instance().update(**params))
    except Exception as e:
        logger.exception("Error updating memory")
        return f"Error: {e}"


@mcp.tool(description="Delete a specific memory by its ID.")
async def delete_memory(memory_id: str) -> str:
    try:
        get_memory_instance().delete(memory_id=memory_id)
        return "Memory deleted."
    except Exception as e:
        logger.exception("Error deleting memory")
        return f"Error: {e}"


@mcp.tool(description="Delete all memories for this user.")
async def delete_all_memories() -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        get_memory_instance().delete_all(user_id=user_id)
        return "All memories deleted."
    except Exception as e:
        logger.exception("Error deleting all memories")
        return f"Error: {e}"


@mcp.tool(description="Delete user/agent/run entities and all their associated memories.")
async def delete_entities(user_id: str | None = None, agent_id: str | None = None, run_id: str | None = None) -> str:
    try:
        m = get_memory_instance()
        deleted = []
        if user_id:
            m.delete_all(user_id=user_id)
            deleted.append(f"user:{user_id}")
        if agent_id:
            m.delete_all(agent_id=agent_id)
            deleted.append(f"agent:{agent_id}")
        if run_id:
            m.delete_all(run_id=run_id)
            deleted.append(f"run:{run_id}")
        if not deleted:
            return "Error: at least one of user_id, agent_id, run_id is required"
        return f"Deleted entities: {', '.join(deleted)}"
    except Exception as e:
        logger.exception("Error deleting entities")
        return f"Error: {e}"


@mcp.tool(description="List all user/agent/run entities.")
async def list_entities() -> str:
    try:
        buckets: dict[tuple[str, str], dict] = {}
        for payload in _iter_payloads():
            created = _parse_timestamp(payload.get("created_at"))
            updated = _parse_timestamp(payload.get("updated_at")) or created
            for entity_type, field in TYPE_TO_FIELD.items():
                value = payload.get(field)
                if not value:
                    continue
                key = (entity_type, str(value))
                bucket = buckets.setdefault(key, {"total_memories": 0, "created_at": None, "updated_at": None})
                bucket["total_memories"] += 1
                if created and (bucket["created_at"] is None or created < bucket["created_at"]):
                    bucket["created_at"] = created
                if updated and (bucket["updated_at"] is None or updated > bucket["updated_at"]):
                    bucket["updated_at"] = updated
        return json.dumps(
            [
                {"id": entity_id, "type": entity_type, **{k: (v.isoformat() if v else None) for k, v in data.items() if k != "total_memories"}, "total_memories": data["total_memories"]}
                for (entity_type, entity_id), data in sorted(buckets.items())
            ]
        )
    except Exception as e:
        logger.exception("Error listing entities")
        return f"Error: {e}"
```

> **Note:** `_iter_payloads`, `_parse_timestamp`, `TYPE_TO_FIELD` need to be
> importable from `routers/entities.py` (drop the leading underscore or add
> them to `__all__` if module-private-by-convention names cause friction —
> minor, decide during implementation).

### 2e. Streamable HTTP route

Copy `openmemory/api/app/mcp_server.py:496-565` (the `capture_send`
ASGI-interception pattern) near-verbatim, adapted for this server's single
`/` path (no `{client_name}`/`{user_id}` path params — identity comes from
headers, not the URL) and with `Depends(verify_auth)` added:

```python
@mcp_router.api_route("/", methods=["POST", "GET", "DELETE"])
async def handle_streamable_http(request: Request, _auth=Depends(verify_auth)):
    try:
        user_id, agent_id = _resolve_identity(request)
    except ValueError as e:
        return Response(status_code=400, content=str(e).encode())

    user_token = user_id_var.set(user_id)
    agent_token = agent_id_var.set(agent_id)

    response_started = False
    response_status = 200
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()

    async def capture_send(message):
        nonlocal response_started, response_status
        if message["type"] == "http.response.start":
            response_started = True
            response_status = message["status"]
            response_headers.extend(message.get("headers", []))
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    try:
        transport = StreamableHTTPServerTransport(mcp_session_id=None, is_json_response_enabled=True)
        async with anyio.create_task_group() as tg:
            async def run_server(*, task_status=anyio.TASK_STATUS_IGNORED):
                async with transport.connect() as (read_stream, write_stream):
                    task_status.started()
                    await mcp._mcp_server.run(
                        read_stream, write_stream,
                        mcp._mcp_server.create_initialization_options(),
                        stateless=True,
                    )
            await tg.start(run_server)
            await transport.handle_request(request.scope, request.receive, capture_send)
            await transport.terminate()
            tg.cancel_scope.cancel()
    finally:
        user_id_var.reset(user_token)
        agent_id_var.reset(agent_token)

    if not response_started:
        return Response(status_code=500, content=b"Transport did not produce a response")
    return Response(content=bytes(response_body), status_code=response_status, headers=dict(response_headers))
```

The `try/finally` around contextvar reset is enforced in code (not just a
comment) — this matters for correctness (not security) since concurrent
requests on the same process must not leak identity into each other.

### 2f. setup function

```python
def setup_mcp_server(app):
    mcp._mcp_server.name = "mem0-mcp-server"
    app.include_router(mcp_router)
```

---

## Step 3: Wire into `server/main.py`

```python
from routers.mcp_server import setup_mcp_server

# After app = FastAPI(...) and other app.include_router(...) calls:
setup_mcp_server(app)
```

The MCP endpoint will be available at `POST http://localhost:8888/mcp/`.

---

## Step 4: Add env vars to `server/.env` / `docker-compose.yaml`

In `server/docker-compose.yaml`, add to the `mem0` service `environment` block:

```yaml
- MEM0_MCP_USER_ID=${MEM0_MCP_USER_ID:-}
- MEM0_MCP_AGENT_ID=${MEM0_MCP_AGENT_ID:-claude-code}
```

In `server/.env`, set:
```
MEM0_MCP_USER_ID=kevin
```

---

## Step 5: Patch the plugin

File: `integrations/mem0-plugin/.mcp.json`

```json
{
  "mcpServers": {
    "mem0": {
      "type": "http",
      "url": "http://localhost:8888/mcp/",
      "headers": {
        "X-API-Key": "${MEM0_API_KEY}",
        "X-Agent-ID": "claude-code",
        "X-User-ID": "${MEM0_USER_ID}"
      }
    }
  }
}
```

Add `MEM0_USER_ID` to your shell environment or `.env` for the plugin to
pick up.

---

## Step 6: Reinstall the plugin locally

```bash
claude plugin uninstall mem0
claude plugin install /Users/kschwaar/sandbox/mem0/integrations/mem0-plugin
```

---

## Verification steps

1. Rebuild and restart the container:
   `docker compose -f server/docker-compose.yaml up mem0 -d --build`
2. Check the MCP endpoint responds:
   ```bash
   curl -X POST http://localhost:8888/mcp/ \
     -H "X-API-Key: <your-key>" \
     -H "X-Agent-ID: claude-code" \
     -H "X-User-ID: kevin" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
   ```
   Expected: JSON response listing all 9 tools.
3. Start a new Claude Code session — the mem0 MCP tools should appear.
4. Test `add_memory` and `search_memories` via Claude Code; confirm a memory
   added without an explicit `X-Agent-ID` is still retrievable from a
   different simulated agent (shared pool, not siloed by agent_id).
5. Verify memories appear in the dashboard at `localhost:3333`.

---

## Key files referenced

- `server/main.py` — router registration pattern, REST equivalents for
  `get_memory`/`update_memory` (lines 465, 508-524)
- `server/auth.py` — `verify_auth` (line 144), reused directly via `Depends`
- `server/server_state.py` — `get_memory_instance()`
- `server/routers/entities.py` — `list_entities`/`delete_entity` logic
  (lines 43-76), reused for the MCP `list_entities`/`delete_entities` tools
- `openmemory/api/app/mcp_server.py` lines 496-565 — Streamable HTTP
  boilerplate (confirmed: plain `api_route`, no `Mount` needed)
- `integrations/mem0-plugin/.opencode-plugin/opencode-mem0.ts` lines
  417-625 — authoritative tool names/signatures to match
- `integrations/mem0-plugin/skills/forget/SKILL.md` — confirms `forget`
  calls `get_memory`/`search_memories`/`delete_memory` (singular)
