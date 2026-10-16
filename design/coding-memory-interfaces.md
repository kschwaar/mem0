# Coding-memory interface catalog and boundary verification

## Scope

Daily use: the installed Mem0 coding-harness plugin, its skills and MCP tool, automatic lifecycle capture, and the standalone CLI for human/agent diagnostics and administration. The pilot API is `http://127.0.0.1:8899`. Dashboard REST dependencies are supported scope; dashboard deployment/browser workflows are deferred. Other native harness packages are inventoried, not assumed verified by an OMP run.

No changes to extraction prompts, model selection, Neo4j, rerankers, or HTTP/MCP transports belong to this phase. No commit, push, or PR is part of verification. The eventual integration target is `personal/main`, not the upstream-mirror `main`.

## Daily interfaces

| Boundary | Entry point / implementation | Contract and dependencies |
| --- | --- | --- |
| Harness lifecycle | Installed `mem0@mem0-plugins`; repository bundle `integrations/claude-code-plugin/`; shared runtime `integrations/agent-plugin-core/python/` | Native host events must capture user/assistant exchanges, retain repository/user/session identity, honor pause, and deliver a detached flush on shutdown/compaction. Installing Claude metadata inside OMP does not register native OMP event handlers. |
| Memory search MCP | Bundle `.mcp.json` and `core/mcp_server.py`; tool `search_memories` | Read-only tool. Required `query`; optional `top_k`, `category`, `scope=repo|dir|mine`, `run_id`. Uses platform-compatible REST search. There are no MCP add/delete tools in this bundle. |
| Plugin controls CLI | Bundle `core/memory_cli.py` | `status --json`, `doctor [--json]`, `pause`, `resume`, `forget [--remote] [--yes] [--include-project-memory]`. Local SQLite/pending queue and remote API are separate state boundaries. Python >=3.10 is required. |
| Standalone daily CLI | Installed Node `@mem0/cli` v0.2.14; source `cli/node/` | `add`, `search`, `get`, `list`, `update`, `delete`, `status`, `entity list/delete`, `event list/status`, `config show/get/set`, `version/init/import`. `config` concerns client configuration, not server `/configure`. Entity deletion and scoped bulk deletion are destructive. |
| Alternate CLI | `cli/python/`, Typer/httpx | Same product command family, Python >=3.10. Inventory only until it is selected for daily use; passing Node CLI checks is not Python CLI verification. |
| Native REST | `server/main.py`, `server/routers/` | Authentication, memory operations, configuration, entities, API keys, auth sessions and request logs. This is a distinct contract from the plugin MCP tool. |
| Server-hosted MCP | `server/routers/platform_compat.py`, `/mcp` | Separate server-side MCP endpoint with its own identity/header resolution. Not the installed plugin's local stdio MCP process. |
| Dashboard | `server/dashboard/` | Depends on REST plus a Next.js refresh-cookie proxy. It is not deployed by `server/compose.pilot.yaml`; UI login, refresh-cookie behavior, and browser actions remain deferred. |

## Plugin skills: six distinct behaviors

Canonical templates live in `integrations/agent-plugin-core/skills/`; installed native `skills/` and `core/` are generated bundles, not independent sources to patch.

| Skill | Actual action | Verification requirement |
| --- | --- | --- |
| `remember` | Restates the requested fact; there is no synchronous write command. Lifecycle hooks must save the exchange. | Observe captured evidence, a real add request, an identified memory, and later recall. Never equate acknowledgment with durable storage. |
| `search` | Calls `search_memories` MCP tool. | Known stored memory is returned; wrong scope/run excludes it; errors are not described as empty results. |
| `status` | Executes plugin CLI `status --json` and `doctor`. | Endpoint, Python, credentials, authentication, capture state and counters correspond to the actual runtime. |
| `pause` | Persists paused state through the plugin CLI. | New exchanges are not captured or saved while paused. |
| `resume` | Clears paused state through the plugin CLI. | Later exchanges are captured; paused activity is not retroactively recorded. |
| `forget` | Executes plugin CLI scoped local/remote deletion after confirmation. | Exercise only in a disposable user/repository namespace, preserving unrelated sentinel memories. This is not a targeted single-ID deletion skill. |

OMP uses `/skill:<name>` (including namespaced names when skills collide), not an assumed Claude `/mem0:<name>` command. `disable-model-invocation: true` hides a skill from automatic model selection; it does not establish that the skill is absent. Installed skill identity and its required environment must be checked rather than inferred from model-visible descriptions.

## Configuration and scope

- Plugin endpoint: `MEM0_API_URL`, with canonical core's lower-precedence `MEM0_BASE_URL` alias, then the hosted default. Explicit localhost routing is required.
- Standalone CLI endpoint: `--base-url`, then `MEM0_BASE_URL` / client configuration. Do not assume it reads `MEM0_API_URL`.
- Credentials stay in protected environment/local files. Do not print API keys, render unredacted Compose configuration, or pass keys as process arguments.
- The standalone CLI sends `Authorization: Token`; the server also supports `X-API-Key` and JWT authentication. The pilot key is admin-capable, so scoped filter checks are not proof of multi-tenant authorization.
- The shared core calculates repository `agent_id`/`app_id`; `user_id` identifies personal memory and `run_id` identifies a session. Shared project memories intentionally omit personal `user_id`.
- Plugin evidence, pause state, cached credentials and handoffs must resolve to the same data directory for lifecycle, skills and MCP. Tests must not silently select a different directory.
- Pilot storage: Qdrant memories, PostgreSQL app/auth/configuration, SQLite history. oMLX and Langfuse are external prerequisites; the pilot health check is authenticated `/configure`, not a nonexistent `/health` route.

## REST contract map

| Consumer / operation | Route |
| --- | --- |
| Plugin/CLI authenticated ping | `GET /v1/ping/` |
| Plugin/CLI add | `POST /v3/memories/add/` |
| Plugin/CLI search | `POST /v3/memories/search/` |
| CLI list | `POST /v3/memories/` with pagination |
| CLI get/update/delete | `GET`, `PUT`, `DELETE /v1/memories/{memory_id}/` |
| CLI scope-wide deletion | `DELETE /v1/memories/` |
| CLI entities | `GET /v1/entities/`; `DELETE /v2/entities/{entity_type}/{entity_id}/` |
| CLI processing events | `GET /v1/events/`; `GET /v1/event/{event_id}/` |
| Native memory operations | `POST`, `GET`, `DELETE /memories`; `GET`, `PUT`, `DELETE /memories/{memory_id}`; `POST /search`; `GET /memories/{memory_id}/history` |
| Plugin forget enumeration | `POST /v2/memories/`, sharing the authenticated `/v3/memories/` list handler and exposing top-level `app_id` for scope selection. |
| Server configuration | `GET`, admin `POST /configure`; `GET /configure/providers` |
| Dashboard user/session | `/auth/setup-status`, `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/me`, `/auth/change-password`, `/auth/onboarding-complete` |
| Dashboard API keys | `GET`, `POST /api-keys`; `DELETE /api-keys/{key_id}` |
| Dashboard entities and logs | `GET /entities`; admin `DELETE /entities/{entity_type}/{entity_id}`; admin `GET /requests` |
| Dashboard-only proxy | Next.js `/api/auth/refresh` handles cookie storage/refresh/deletion; `/api/health` checks its backend dependency. Neither is a pilot API route. |

The standalone CLI does not currently expose API-key rotation, user administration, or server configuration as first-class commands. Those remain REST/dashboard administration, not capabilities to invent while testing CLI CRUD.

## Bounded execution order

| Unit | Worker scope | Small acceptance check / stop condition |
| --- | --- | --- |
| B1: host capture | OMP native adapter only | One real session captures its prompt/visible final answer and triggers shutdown handoff. Stop and repair only this boundary if no events are recorded. |
| B2: delivery and fresh recall | Same adapter, shared core and local MCP | One synthetic personal preference reaches the real pilot and is retrieved in a fresh session. Check exact memory ID/text and exclude a different run/scope. |
| B3: skill controls | Isolated local data and test namespaces | Status/doctor agree with runtime; pause suppresses capture; resume restores it; remember saves after lifecycle; forget removes only the disposable namespace. |
| B4: daily CLI | Installed Node binary, one test user/app/run | Status; one `--no-infer` add; list/get/search; update/get; dry-run if supported; delete exact ID; verify absence and unrelated scope exclusion. `--no-infer` still uses the real embedder. |
| B5: admin REST | Read-only authenticated pilot requests | Valid/invalid auth, redacted configuration/providers, API-key/entity visibility and bounded request logs. Do not bootstrap or alter real users. Dashboard browser workflows deferred. |
| B6: review and final gate | Fresh reviewer and supervisor | Relevant native adapter/build/core checks plus re-exercise the fixed real lifecycle. No full benchmark suites. |

Use unique nonces, explicit localhost endpoints, per-command timeouts, bounded waits and receipts containing created IDs. Cleanup only positively identified test data. Never use repository-wide deletion against the human's normal namespace. Record failures with the exact boundary and request evidence; do not silently retry or substitute a direct API write for automatic capture.

## Baseline evidence

Before repair, a real GPT-6-Luna OMP session exited normally but produced zero observed capture/flush events and no API add request. Searches and authentication succeeded. Docker logs separately contained an earlier successful add, so that absence is not evidence of an incapable extractor or failed vector store. The installed bundle had Claude event registrations but no OMP-native extension entry point. The shell's `python3` was 3.9; a supported repository `.venv/bin/python` is already available.

## Verified pilot boundaries

These are small integration checks against the running localhost pilot, not benchmark results or a production-readiness certification.

| Unit | Result | Exercised evidence |
| --- | --- | --- |
| B1: OMP lifecycle | Pass | Real GPT-6-Luna sessions captured prompts and visible replies; shutdown produced a `semantic-succeeded` flush and an identified memory containing the unique preference. Normal `.omp/config.yml` loading worked without `--extension`. |
| B2: fresh recall and scope | Pass | A fresh OMP session invoked the registered MCP search and returned the saved label. Real stdio MCP checks passed own-private, other-user exclusion, repository-shared, directory inclusion/exclusion and wrong-run exclusion. Real-Qdrant offline coverage additionally excludes a prefix sibling such as `src-other` from `src`. |
| B3: all six skills | Pass | Actual skill reads and their prescribed actions were observed. Valid-key doctor succeeded; invalid-key doctor failed authentication. Pause suppressed new capture, resume restored it without replay. Forget removed the test user's repository memory while retaining both an other-repository sentinel and a shared-project sentinel; both sentinels were then deleted by exact ID. |
| B4: installed CLI CRUD | Pass | Node CLI v0.2.14: localhost status, one `--no-infer` add, exact-text list/get, search, other-app exclusion, dry-run preservation, update/get, exact-ID delete and absence check. |
| B4: CLI diagnostics | Pass | Configuration endpoint and credential redaction, known test-user entity visibility, known add-event ID in the event list, and matching successful event status. The installed event-list envelope omits user/run fields; the probe correlated the ID through REST rather than assuming those fields exist. |
| B5: admin REST | Pass, limited | Invalid keys rejected; configuration redacted; provider catalog, entity listing and bounded request logs reachable. Dashboard setup is still required; API-key administration was not bootstrapped or mutated. |
| B6: automated checks | Pass | 204 offline native Python tests; 14 API compatibility tests in an ephemeral container using the pilot image; JavaScript syntax check; all five native bundle checks and the portable bundle check; root Ruff/isort pre-commit hooks; documentation index coverage. |
| Cleanup | Pass | All 12 positively identified synthetic memory IDs returned HTTP 404 after cleanup. No blanket reset or deletion of the human's normal namespace was used. |

The API suite retains three deprecation warnings from existing Starlette/AnyIO, Passlib and Pydantic paths. The disposable test container installed pytest only for the run; the production container's packages were not changed.

## Repaired contracts and causes

| Failure | Root cause | Boundary repair |
| --- | --- | --- |
| OMP could search but did not automatically save | Claude hook declarations were not native OMP event registrations. | Added the bundle's OMP extension entrypoint and thin Python shim; translated native session, file/tool and error events into the existing shared runtime. |
| Forget first returned 404, then selected zero memories | The pilot lacked `/v2/memories/`; SDK listing left `app_id` under metadata while the plugin selects by top-level `app_id`. | Added the authenticated list alias and promoted the scope field without removing metadata. |
| Directory-scoped MCP search failed | Hosted `metadata.dirs.contains` was not a valid OSS payload predicate. | Unwrapped hosted metadata at the REST boundary and mapped directory-chain membership to exact equality. Other string `contains` predicates remain unchanged. Duplicate flattened keys return HTTP 400, never silently overwrite a constraint. |
| Entity administration appeared empty | Qdrant returns `(records, cursor)`; enumeration only unwrapped a nested list. | Reused one tuple/list-aware row normalizer for entity enumeration and existing row consumers. |
| Deleted IDs appeared to exist | SDK `get()` returns `None`; compatibility GET serialized it as HTTP 200 `null`. | Missing-ID GET now returns HTTP 404. |
| Large forget scopes could silently truncate | Server prefix fetching stopped at 1,000; the plugin's bounded page loop could finish without proving exhaustion. | Removed the server's total-prefix cap and made the plugin fail before remote deletion when its page limit is exhausted. The guard regression failed before the fix and passes afterward. |

The forget client still bounds enumeration to 50 pages of 100. If every page is full, it refuses remote deletion because it cannot prove exhaustion, including the ambiguous exactly-5,000 case. Deep API pagination currently fetches a growing prefix because the SDK has no offset/cursor contract; performance and true cursor pagination were not redesigned.

These failures were interface-registration, serialization, filtering and pagination mismatches. No extraction prompt, inference model, embedding model or Neo4j configuration was changed to make them pass. A successful synthetic preference does not measure memory quality on realistic coding history.

## Activation and reproducible checks

The repaired OMP bundle is the repository source at `integrations/claude-code-plugin`, loaded by this checkout's local `.omp/config.yml`. The previously installed marketplace cache was not patched or published. Start a new session using the shared environment documented in that bundle's README; loading MCP alone is not sufficient for capture.

Run from the repository root:

```fish
.venv/bin/python -m pytest integrations/claude-code-plugin/tests --ignore=integrations/claude-code-plugin/tests/integration -q
node --check integrations/claude-code-plugin/adapters/omp/index.mjs
for host in claude-code cursor codex kimi antigravity
    .venv/bin/python integrations/agent-plugin-core/build/build.py $host --kind native --check
end
.venv/bin/python integrations/agent-plugin-core/build/build.py mem0-agent-plugin --kind portable --check
.venv/bin/python scripts/check-llms-txt-coverage.py
```

Server tests must use Docker, not the root environment, which lacks FastAPI:

```fish
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml run --rm --no-deps \
    -e MEM0_TELEMETRY=false -e MEM0_RUNTIME_TRACING=false \
    -v (pwd)/server/tests:/app/tests:ro --entrypoint sh mem0 \
    -c 'python -m pip install --quiet --disable-pip-version-check pytest && python -m pytest /app/tests/test_platform_compat.py -q'
```

The supervisor reran the discriminating checks rather than accepting worker completion claims. An independent review identified the server's 1,000-record cutoff; the repaired API received a fresh source review with no blocking findings. Some worker attempts failed due to probe assumptions or incomplete reads, not product failures; their claims were rejected or taken over by the supervisor.

## Deliberately unverified / deferred

- Dashboard deployment, initial user setup, browser login and refresh cookies; API-key rotation and other admin mutations.
- Python CLI, other native harness runtimes, sidekick workflows, and the separate server-hosted MCP endpoint. Inventory and generated-bundle consistency are not live-host verification.
- Full tenant authorization: the pilot credential is admin-capable. Filter isolation is not an authorization audit.
- Scheduled idle/long-running-session behavior under load, extraction quality, prompt/model tuning, graph storage, transport redesign and latency/throughput characterization.
- LoCoMo, LongMemEval, BEAM and comparisons with Mem0's published scores. None were run in this boundary-verification phase.

The requested lifecycle-manager supervisor workspace (`workspace:1000000001`, UUID `FC4D156D-6868-4F2F-8402-1982BF82DFC1`) is absent from the current cmux workspace list. Work was organized in bounded units and the local task ledger; no unrelated workspace was treated as a replacement.

