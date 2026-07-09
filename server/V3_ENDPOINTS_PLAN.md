# Plan: Add /v3/* Endpoint Support to `server/`

## Status: Implemented and verified (2026-07-01)

`server/routers/v3.py` implements all 3 endpoints, wired into `main.py`, and
confirmed working end-to-end via curl (add/list/search all round-tripped
correctly using the exact `AND`+`app_id` filter shape and `Authorization:
Token` header the plugin's hook scripts send).

Two deviations from the original plan, both found during implementation:

1. **Filter translator does more than flatten AND.** The naive
   scalar-flattening in the original Step 2a would have passed an `app_id`
   condition straight through to `Memory.search()`/`get_all()`. Self-hosted
   `Memory.add()` has no `app_id` parameter, so `app_id` is never actually
   present in a stored payload — filtering on it wouldn't error, it would
   silently match nothing, forever. Same class of bug as the `agent_id`
   truthy-gated-filter trap already called out in `MCP_PLAN.md`. Fixed by
   dropping `app_id` entirely and promoting `metadata.<key>` sub-conditions to
   real top-level scalar filters instead (metadata values *are* stored flat in
   the payload, so this direction is safe). See the real implementation in
   Step 2a below.

2. **Auth scheme gap: `Authorization: Token <key>`.** Every plugin hook
   script (`auto_import.py`, `_search.py`, `auto_capture.py`, etc. — all 8 of
   them) sends `Authorization: Token <api_key>`, the real Platform API's own
   auth scheme, never `X-API-Key`. `server/auth.py`'s `verify_auth` only
   recognized `Authorization: Bearer <jwt>` (via `HTTPBearer`, which returns
   `None` for a non-Bearer scheme rather than erroring) or `X-API-Key`. Without
   a fix, every v3 request from a hook script would 401 even with the routes
   in place. Fixed with a small addition to `verify_auth`: if no `X-API-Key`
   header and the `Authorization` header starts with `Token ` (case
   insensitive), treat the rest of the value as an API key exactly like
   `X-API-Key`. Verified this doesn't weaken auth — non-Bearer/non-Token
   schemes still 401, and the existing `X-API-Key`/JWT paths are unaffected.

## Goal

Add v3-compatible REST endpoints to the self-hosted `server/` so that the
mem0 CLI (`mem0-cli`) and the `MemoryClient` SDK can target localhost instead
of `api.mem0.ai`. Also unblocks the plugin lifecycle hooks (auto-capture,
session summaries, search) which all call v3 endpoints.

---

## Background

The CLI and SDK call exactly 3 v3 endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v3/memories/add/` | Add memories |
| `POST` | `/v3/memories/` | List memories (paginated, filter in body) |
| `POST` | `/v3/memories/search/` | Search memories |

The current server has equivalent functionality at:
- `POST /memories` — add
- `GET /memories` — list (filters as query params)
- `POST /search` — search

The v3 contract differs in two ways:
1. **List and search** take entity filters in the request **body** as a
   structured `filters` dict (supporting AND/OR nesting), not query params
2. **Add** is nearly identical — just a different path

---

## Step 1: Understand the v3 filter schema

The CLI builds filters like this (from `cli/python/src/mem0_cli/backend/platform.py`):

```python
# Single entity — flat dict:
{"user_id": "kevin"}

# Multiple entities — AND list:
{"AND": [{"user_id": "kevin"}, {"agent_id": "claude-code"}]}

# With date range:
{"AND": [{"user_id": "kevin"}, {"created_at": {"gte": "2026-01-01"}}]}
```

You need a filter translator function that converts this into the `filters`
dict format that `get_memory_instance().search()` and `.get_all()` accept.

Study `mem0/memory/main.py` to understand what filter shapes the OSS `Memory`
class accepts natively — it likely accepts flat `{"user_id": "x"}` dicts
already. AND/OR nesting may need to be flattened for simple cases (the CLI
only ANDs entity IDs, never ORs them).

---

## Step 2: Create `server/routers/v3.py`

New file. Contains the three v3 route handlers plus the filter translator.

### 2a. Filter translator

> **Deviation from the original draft below (kept for context) — see the
> "Status" section at the top.** `mem0/memory/main.py`'s `Memory.search()`
> and `.get_all()` both natively support `AND`/`OR`/`NOT` and rich operators
> (`eq`/`ne`/`in`/`gt`/`contains`/...) *inside* the vector store's filter
> compiler — but both methods separately gate on `user_id`/`agent_id`/`run_id`
> appearing as a **literal top-level key** of the filters dict before that
> compiler ever runs. An `{"AND": [{"user_id": "x"}, ...]}` wrapper (the shape
> every v3 client sends) fails that top-level check even though the compiler
> itself could handle it — so flattening onto top-level keys is still
> required, just not for the reason originally assumed (it's a validation-gate
> workaround, not a missing-feature workaround).
>
> The actual implementation (`server/routers/v3.py`) also explicitly drops
> `app_id` (never stored — see Status section) and promotes `metadata.<key>`
> sub-conditions to top-level scalar filters (metadata values are stored flat
> in the payload, so this is a safe, useful translation the original draft
> didn't include):
>
> ```python
> _ENTITY_KEYS = ("user_id", "agent_id", "run_id")
>
> def translate_v3_filters(filters):
>     if not filters:
>         return {}
>     if "AND" not in filters and "OR" not in filters:
>         return {k: v for k, v in filters.items() if k != "app_id"}
>     if "AND" in filters:
>         result = {}
>         for condition in filters["AND"]:
>             for key, value in condition.items():
>                 if key == "app_id":
>                     continue
>                 if key in _ENTITY_KEYS and isinstance(value, str):
>                     result[key] = value
>                 elif key == "metadata" and isinstance(value, dict):
>                     for meta_key, meta_value in value.items():
>                         if isinstance(meta_value, str):
>                             result[meta_key] = meta_value
>         return result
>     logger.warning("v3 OR filter not supported on self-hosted server, ignoring: %r", filters)
>     return {}
> ```

Original draft (superseded by the above):

```python
from typing import Any

def translate_v3_filters(filters: dict | None) -> dict:
    """
    Convert v3 filter shape to the flat dict that Memory.search/get_all accept.
    
    v3 sends:
      {"user_id": "kevin"}                          # flat — pass through
      {"AND": [{"user_id": "x"}, {"agent_id": "y"}]}  # flatten AND list
    
    Nested OR conditions and date range filters (created_at gte/lte) are
    passed through as-is if Memory supports them, otherwise dropped with a
    warning log.
    """
    if not filters:
        return {}
    
    # Already flat
    if "AND" not in filters and "OR" not in filters:
        return filters
    
    # Flatten simple AND — extract scalar equality conditions
    if "AND" in filters:
        result = {}
        for condition in filters["AND"]:
            # Only flatten simple {key: scalar_value} conditions
            for k, v in condition.items():
                if isinstance(v, str):
                    result[k] = v
                # Non-string values (date ranges, etc.) — log and skip for now
        return result
    
    # OR conditions — not supported natively, log warning, return empty
    import logging
    logging.warning("v3 OR filter not supported on self-hosted server, ignoring")
    return {}
```

> **Important:** Before finalising this, check what filter shapes
> `get_memory_instance().search()` and `.get_all()` actually accept by reading
> `mem0/memory/main.py`. If the OSS layer already supports AND/OR natively,
> pass the filters through unchanged instead of translating.

### 2b. Request models

```python
from typing import Any, Optional
from pydantic import BaseModel

class V3AddRequest(BaseModel):
    messages: Optional[list[dict]] = None
    text: Optional[str] = None          # alternative to messages
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[dict] = None
    infer: bool = True
    immutable: bool = False
    expiration_date: Optional[str] = None
    categories: Optional[list[str]] = None
    source: Optional[str] = None        # CLI sends "CLI" — ignore it

class V3ListRequest(BaseModel):
    filters: Optional[dict[str, Any]] = None
    source: Optional[str] = None

class V3SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    threshold: float = 0.3
    filters: Optional[dict[str, Any]] = None
    rerank: bool = False
    keyword_search: bool = False
    fields: Optional[list[str]] = None
    source: Optional[str] = None
```

### 2c. Route handlers

```python
from fastapi import APIRouter, Depends, Query
from auth import verify_auth
from server_state import get_memory_instance
from .v3 import translate_v3_filters, V3AddRequest, V3ListRequest, V3SearchRequest

router = APIRouter(prefix="/v3", tags=["v3"])


@router.post("/memories/add/")
def v3_add_memory(req: V3AddRequest, _auth=Depends(verify_auth)):
    # Require at least one entity identifier
    if not any([req.user_id, req.agent_id, req.run_id]):
        raise HTTPException(status_code=400, detail="At least one of user_id, agent_id, run_id is required.")
    
    params = {}
    if req.user_id:   params["user_id"] = req.user_id
    if req.agent_id:  params["agent_id"] = req.agent_id
    if req.run_id:    params["run_id"] = req.run_id
    if req.metadata:  params["metadata"] = req.metadata
    if not req.infer: params["infer"] = False
    
    # messages or text — normalise to messages format
    messages = req.messages or [{"role": "user", "content": req.text}]
    
    return get_memory_instance().add(messages, **params)


@router.post("/memories/")
def v3_list_memories(
    req: V3ListRequest,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _auth=Depends(verify_auth),
):
    filters = translate_v3_filters(req.filters)
    if not filters:
        raise HTTPException(status_code=400, detail="filters are required for listing memories.")
    
    result = get_memory_instance().get_all(filters=filters)
    
    # Wrap in paginated response shape the CLI expects
    if isinstance(result, dict) and "results" in result:
        items = result["results"]
    elif isinstance(result, list):
        items = result
    else:
        items = []
    
    # Simple slice-based pagination
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "results": items[start:end],
        "count": len(items),
        "page": page,
        "page_size": page_size,
    }


@router.post("/memories/search/")
def v3_search_memories(req: V3SearchRequest, _auth=Depends(verify_auth)):
    filters = translate_v3_filters(req.filters)
    
    params = {"limit": req.top_k}
    if filters:
        params["filters"] = filters
    
    return get_memory_instance().search(query=req.query, **params)
```

---

## Step 2.5 (added during implementation): Accept `Authorization: Token <key>` in `server/auth.py`

Not in the original plan — discovered when the plugin hooks still 401'd
against a fully-implemented v3 router. All 8 plugin hook scripts (and the
`mem0ai` SDK's own `MemoryClient`) authenticate with
`Authorization: Token <api_key>`, matching the real Platform API's DRF-style
token auth. `verify_auth`'s `HTTPBearer` dependency only recognizes the
`Bearer` scheme (returning `None`, not erroring, for anything else), and
nothing else was listening for `Authorization` at all — so these requests
fell through to the "no credentials, `AUTH_DISABLED` is false" 401 branch
regardless of whether `/v3/*` existed.

Fix in `verify_auth` (`server/auth.py`): if no `X-API-Key` header was sent,
check whether `Authorization` starts with `Token ` (case-insensitively) and,
if so, treat the remainder as the API key exactly like `X-API-Key`:

```python
if x_api_key is None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("token "):
        x_api_key = auth_header[len("Token "):].strip()

if x_api_key is not None:
    ...  # unchanged
```

Verified this doesn't weaken auth: a garbage `Authorization` scheme still
401s, and the existing `Bearer`/`X-API-Key`/`AUTH_DISABLED` paths are
unaffected.

---

## Step 3: Wire into `server/main.py`

Add near the other router registrations:

```python
from routers import v3 as v3_router
app.include_router(v3_router.router)
```

---

## Step 4: Test with the CLI

### 4a. Point the CLI at localhost

The Python CLI reads `MEM0_BASE_URL` (or `platform.base_url` in its config).
Set it in your shell:

```bash
export MEM0_BASE_URL="http://localhost:8888"
```

Or configure it permanently:
```bash
mem0 config set platform.base_url http://localhost:8888
```

### 4b. Test add

```bash
mem0 add "I prefer Python for scripting tasks" --user-id kevin
```

Expected: memory added, ID printed.

### 4c. Test list

```bash
mem0 list --user-id kevin
```

Expected: table showing the stored memory with content and metadata.

### 4d. Test search

```bash
mem0 search "scripting language preferences" --user-id kevin
```

Expected: the added memory returned with a relevance score.

### 4e. Verify in dashboard

Open `http://localhost:3333` and confirm the memory appears with correct
user, agent, and content fields.

---

## Step 5: Test with the plugin lifecycle hooks

With `MEM0_BASE_URL=http://localhost:8888` in your environment:

1. Start a new Claude Code session
2. Check `~/.mem0/hooks.log` — hook errors about v3 endpoints should now be gone
3. End the session — the `on_stop.sh` hook calls `capture_session_summary.py`
   which POSTs to `/v3/memories/add/`. Verify the summary memory appears in
   the dashboard.

---

## Verification curl commands

```bash
API_KEY="m0sk_KwyRmEwtiI42qW-WJs60F3LpeUpEDX5C2wrezaqxCHM"

# Add
curl -s -X POST http://localhost:8888/v3/memories/add/ \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"I like Go for CLI tools"}],"user_id":"kevin"}' | python3 -m json.tool

# List
curl -s -X POST http://localhost:8888/v3/memories/ \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"filters":{"user_id":"kevin"}}' | python3 -m json.tool

# Search
curl -s -X POST http://localhost:8888/v3/memories/search/ \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"language preferences","filters":{"user_id":"kevin"}}' | python3 -m json.tool
```

---

## Key files to read before starting

- `cli/python/src/mem0_cli/backend/platform.py` — exact payloads the CLI sends
- `mem0/memory/main.py` — what filter shapes `Memory.search()` and `Memory.get_all()` natively accept (determines how much translation is actually needed)
- `server/main.py` — how existing `/memories` and `/search` routes work, to mirror their logic
- `server/routers/entities.py` — pattern for a clean router file in this codebase
