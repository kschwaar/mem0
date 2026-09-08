# Localhost CLI and MCP Compatibility

This note maps the API contracts needed to make the `server/` self-hosted stack
usable as the memory backend for local coding agents and local AI applications.
The intended clients are the native and portable agent plugins built from
`integrations/agent-plugin-core`; OpenMemory is not a target for this work.

## Historical gap addressed by the adapter

Before the downstream compatibility adapter was added, the repository had two
separate localhost-oriented stacks:

- `server/`: the newer self-hosted FastAPI server plus dashboard. It exposes
  synchronous OSS-style REST endpoints such as `POST /memories` and
  `POST /search`.
- `openmemory/`: the older local OpenMemory app. It exposes MCP routes under
  `/mcp`, but has its own app/user/access-log database model and is marked as
  being sunset in its README.

The hosted Mem0 Platform exposes a different public surface:

- Platform REST uses `/v3/memories/add/`, `/v3/memories/search/`, and
  `/v3/memories/`, plus `/v1/...` management routes.
- Platform MCP is hosted at `https://mcp.mem0.ai/mcp` and exposes tools such as
  `add_memory`, `search_memories`, `get_memories`, `get_memory`, `update_memory`,
  `delete_memory`, `delete_all_memories`, `delete_entities`, `list_entities`,
  `list_events`, and `get_event_status`.

The missing routes were therefore not accidental typos. The adapter implemented
the compatible REST and MCP surfaces directly in `server/`; OpenMemory was not
revived for this purpose.

## Existing self-hosted server contract

Authentication accepts:

- `Authorization: Bearer <dashboard JWT>`
- `X-API-Key: <self-hosted API key>`
- `X-API-Key: <ADMIN_API_KEY>` when configured
- no auth only when `AUTH_DISABLED=true`

Memory routes:

| Route | Request | Response notes |
| --- | --- | --- |
| `POST /memories` | `{messages, user_id?, agent_id?, run_id?, metadata?, expiration_date?, infer?, memory_type?, prompt?}` | Calls `Memory.add(...)` synchronously and returns its result. Requires at least one of `user_id`, `agent_id`, `run_id`. |
| `POST /search` | `{query, filters?, user_id?, agent_id?, run_id?, top_k?, threshold?, explain?, show_expired?}` | Calls `Memory.search(...)`. Top-level IDs are deprecated locally but accepted. |
| `GET /memories` | query params `user_id?`, `agent_id?`, `run_id?`, `top_k?`, `show_expired?` | Scoped list via `Memory.get_all(...)`; all-memory list is admin-only. |
| `GET /memories/{memory_id}` | none | Calls `Memory.get(...)`. |
| `PUT /memories/{memory_id}` | `{text?, metadata?, expiration_date?}` | Maps `text` to `Memory.update(text=...)`. |
| `GET /memories/{memory_id}/history` | none | Calls `Memory.history(...)`. |
| `DELETE /memories/{memory_id}` | none | Calls `Memory.delete(...)`. |
| `DELETE /memories` | query params `user_id?`, `agent_id?`, `run_id?` | Admin-only scoped delete via `Memory.delete_all(...)`. |
| `POST /reset` | none | Admin-only full reset. |

Management routes:

| Route | Notes |
| --- | --- |
| `GET /auth/setup-status`, `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET/PATCH /auth/me`, `POST /auth/change-password` | Dashboard auth and setup. |
| `GET /api-keys`, `POST /api-keys`, `DELETE /api-keys/{key_id}` | Per-user self-hosted API keys. Created keys have prefix `m0sk_...`. |
| `GET /entities` | Lists `user`, `agent`, and `run` entities only. No `app` entity support yet. |
| `DELETE /entities/{entity_type}/{entity_id}` | Deletes `user`, `agent`, or `run` entities only. |
| `GET /requests` | Admin-only API request log for dashboard. |
| `GET/POST /configure`, `GET /configure/providers` | Runtime Mem0 config. |

## Node CLI contract

The `mem0` CLI can be pointed at localhost without code changes:

```bash
mem0 config set platform.base_url http://localhost:8888
# or per command:
mem0 status --base-url http://localhost:8888 --api-key '<key>'
# or via env:
MEM0_BASE_URL=http://localhost:8888 MEM0_API_KEY='<key>' mem0 status
```

Its HTTP backend sends:

- `Authorization: Token <MEM0_API_KEY>`
- `Content-Type: application/json`
- `X-Mem0-Source: cli`
- `X-Mem0-Client-Language: node`
- `X-Mem0-Client-Version: <cli version>`
- `X-Mem0-Caller-Type: user|agent`

Expected CLI endpoints:

| CLI operation | Method + path | Body/query shape |
| --- | --- | --- |
| validate/status | `GET /v1/ping/` | no body. Should return JSON, ideally including `user_email` when known. |
| add | `POST /v3/memories/add/` | `{messages, user_id?, agent_id?, app_id?, run_id?, metadata?, immutable?, infer?, expiration_date?, categories?, source:"CLI"}` |
| search | `POST /v3/memories/search/` | `{query, top_k, threshold, filters?, rerank?, keyword_search?, fields?, source:"CLI"}` |
| list | `POST /v3/memories/?page=&page_size=` | `{filters?, source:"CLI"}` |
| get | `GET /v1/memories/{id}/?source=CLI` | no body |
| update | `PUT /v1/memories/{id}/` | `{text?, metadata?, source:"CLI"}` |
| delete one | `DELETE /v1/memories/{id}/?source=CLI` | no body |
| delete scoped | `DELETE /v1/memories/?source=CLI&user_id=&agent_id=&app_id=&run_id=` | no body |
| list entities | `GET /v1/entities/` | CLI filters returned records by `type` in `{user, agent, app, run}`. |
| delete entity | `DELETE /v2/entities/{user|agent|app|run}/{id}/?source=CLI` | no body |
| list events | `GET /v1/events/` | returns array or `{results:[...]}` |
| event status | `GET /v1/event/{event_id}/` | returns event record |

The original gaps addressed by the adapter were:

- Auth header mismatch: CLI sends `Authorization: Token ...`; server accepts
  `X-API-Key` or Bearer JWT.
- Missing `GET /v1/ping/`.
- Missing `/v3/memories/add/`, `/v3/memories/search/`, `/v3/memories/`.
- Missing `/v1/memories/...` compatibility routes.
- Missing `/v1/entities/`, `/v2/entities/...`.
- The server did not support `app_id`; the CLI and plugins use `app_id` heavily.
- The server had no async event table; Platform `add` returns `PENDING` and an
  `event_id`, while the self-hosted server currently returns the synchronous
  `Memory.add(...)` result. The CLI tolerates both for add display, but `mem0
  event ...` needs event routes if we want full compatibility.

## Plugin and MCP contract

The current agent-plugin family bundles a shared runtime from
`integrations/agent-plugin-core` into native editor-specific directories and a
portable `integrations/mem0-agent-plugin` package:

- Each plugin runs a local stdio MCP server rather than pointing its manifest at
  the hosted remote MCP endpoint.
- Hooks and MCP tools use `MEM0_API_URL`, defaulting to
  `https://api.mem0.ai`. This downstream branch also accepts `MEM0_BASE_URL` as
  a compatibility alias.
- Requests use the same `Authorization: Token <MEM0_API_KEY>` header as the CLI.
- Memory metadata uses `app_id` as project scope and `run_id` as session scope.

The official hosted MCP tool names are:

- `add_memory`
- `search_memories`
- `get_memories`
- `get_memory`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `delete_entities`
- `list_entities`
- `list_events`
- `get_event_status`

OpenMemory's local MCP implementation is useful only as historical evidence that
the repo once carried local MCP code. It is not the target implementation and
should not be used as the runtime MCP service. Its tool names and routes are not
a drop-in match:

- Routes:
  - SSE: `GET /mcp/{client_name}/sse/{user_id}`
  - Streamable HTTP: `/{client_name}/http/{user_id}` under the `/mcp` prefix
- Tools:
  - `add_memories(text, infer=True)`
  - `search_memory(query)`
  - `list_memories()`
  - `delete_memories(memory_ids)`
  - `delete_all_memories()`

The current plugin family uses a shared local runtime and a stdio MCP server.
Both hooks and MCP tools therefore use the same REST endpoint. Set
`MEM0_API_URL` to call localhost; this downstream branch also honors the CLI's
`MEM0_BASE_URL` name as a backward-compatible alias. `MEM0_API_URL` takes
precedence when both are set.

## Implemented server adapter

Compatibility is implemented in `server/` rather than by changing the installed
CLI. This keeps local apps, the CLI, and the plugin aimed at one localhost
service.

Implemented:

- Auth compatibility:
  - `Authorization: Token <key>` is accepted as equivalent to `X-API-Key`.
  - Bearer JWT behavior is unchanged.
- `app_id` support:
  - Accepted in create/search/list/delete payloads and filters.
  - Stored as payload metadata for the OSS SDK and serialized back as top-level
    `app_id` for compatibility.
- Platform-compatible REST aliases:
   - `GET /v1/ping/`
   - `POST /v3/memories/add/`
   - `POST /v3/memories/search/`
   - `POST /v3/memories/`
   - `GET/PUT/DELETE /v1/memories/{id}/`
   - `GET /v1/memories/{id}/history/`
   - `DELETE /v1/memories/`
   - `GET /v1/entities/`
   - `DELETE /v2/entities/{type}/{id}/`
   - `GET /v1/events/`
   - `GET /v1/event/{id}/`
- Minimal event compatibility:
  - `/v3/memories/add/` creates synthetic synchronous `SUCCEEDED` events.
  - `/v1/events/` and `/v1/event/{id}/` expose those records.
- MCP:
  - Implemented directly under `server/` at `POST/GET/DELETE /mcp`.
  - Exposes hosted-compatible tool names listed above.
  - Reuses server auth.
- Agent plugins:
  - Shared behavior lives in `integrations/agent-plugin-core` and is bundled
    into the native plugin directories.
  - Hooks and the local stdio MCP server use `MEM0_API_URL`, defaulting to
    hosted Mem0.
  - `MEM0_BASE_URL` remains available as a downstream compatibility alias.
  - Local use: `MEM0_API_URL=http://localhost:8888`.

Local compatibility no-ops: hosted-only fields such as `rerank`,
`keyword_search`, `fields`, `categories`, `immutable`, and `source` are accepted
where the CLI/plugin send them. Fields without an OSS equivalent are ignored.

## Minimal local target

The smallest useful target for local coding agents is:

- CLI works for `status`, `add`, `search`, `list`, `get`, `update`, `delete`.
- The native agent plugins run their bundled stdio MCP server against
  `MEM0_API_URL=http://localhost:8888`.
- Plugin hooks use the same `MEM0_API_URL` endpoint.
- MCP exposes at least `add_memory`, `search_memories`, `get_memories`, and
  `delete_memory` at `http://localhost:8888/mcp`.

Events, entity deletion, categories, reranking, keyword search, and full hosted
Platform parity can follow after the core loop works.
