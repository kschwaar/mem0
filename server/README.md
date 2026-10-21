# Mem0 Self-Hosted Server

Mem0 ships a self-hosted FastAPI server plus a local dashboard. It is secure by default, supports dashboard login and API keys, and exposes OpenAPI docs at `/docs`.

> **Upgrading?** The Postgres image changed from the archived `ankane/pgvector:v0.5.1`
> to the official `pgvector/pgvector:pg17`, and `POSTGRES_PASSWORD` is now a required
> env var. If you have an existing install, see
> [Migrating from ankane/pgvector to pgvector/pgvector](#migrating-from-ankanepgvector-to-pgvectorpgvector)
> before running `docker compose up`.

## Quick Start

### Prerequisites

Copy the example env file and set a Postgres password (required):

```bash
cd server
cp .env.example .env
# Edit .env — at minimum set POSTGRES_PASSWORD and OPENAI_API_KEY
```

### Agent-first

Run one command; the terminal prints the admin email, password, and first API key.

```bash
cd server
make bootstrap
```

This starts the stack, waits for the API and dashboard to be ready, creates the first admin, and generates the first API key.

> The generated credentials print once in the `=== Ready ===` block. Save the password and API key before closing the terminal — the API key cannot be recovered afterwards.

> `make bootstrap` skips the setup wizard, so the use-case → custom-instructions step doesn't run. To add custom instructions afterwards, `POST /configure` with `{"custom_instructions": "..."}`, or run the Browser-first flow on a fresh install.

You can override the generated credentials:

```bash
cd server
make bootstrap EMAIL=admin@company.com PASSWORD='strong-password' NAME='Admin'
```

For machine-readable output:

```bash
cd server
OUTPUT=json make seed
```

Teardown:

```bash
# Stop the stack
cd server && make down

# Wipe all data (including the Postgres volume)
cd server && make clean
```

### Browser-first

Start the stack and finish setup by walking through the wizard in your browser.

```bash
cd server
make up
```

Then open `http://localhost:3000` and complete the setup wizard.

## Security Defaults

- Dashboard login uses JWTs.
- Programmatic access uses `X-API-Key`.
- Auth is enabled by default.
- `AUTH_DISABLED=true` exists for local development only and should not be used in production.

## Forgotten password

Reset an admin password from the host while the stack is running:

```bash
cd server
make reset-admin-password EMAIL=admin@example.com PASSWORD='new-strong-password'
```

This is the supported recovery path. Anyone with shell access to the host already has full access to the database and secrets, so this command does not expand the attack surface.

## Request log retention

The `request_logs` table is append-only and grows with traffic (~864k rows/day at 10 req/s). Prune it periodically:

```bash
cd server
make prune-logs                               # defaults to 30 days
make prune-logs REQUEST_LOG_RETENTION_DAYS=7  # shorter window
```

Wire the command into cron or a systemd timer in production. The `created_at` column uses a BRIN index, so range deletes stay cheap even on large tables.

## Local URLs

- Dashboard: `http://localhost:3000`
- API: `http://localhost:8888`
- OpenAPI docs: `http://localhost:8888/docs`
- MCP endpoint: `http://localhost:8888/mcp`

For native `GET /memories/{memory_id}`, a missing memory returns HTTP 404. A
storage or provider failure remains HTTP 502; it is not reported as not found.

The hosted MCP endpoint is stateless Streamable HTTP: send JSON-RPC requests
with POST to `/mcp` or `/mcp/`. It does not open a server-initiated SSE stream;
GET returns HTTP 405 with `Allow: POST` instead of leaving the request hanging.
Clients do not need a session ID, and session DELETE is unsupported.

Compose passes `DASHBOARD_URL` (default `http://localhost:3000`) to both the API
and dashboard. Explicit loopback HTTP origins use a non-Secure refresh cookie
for local login/refresh. HTTPS and non-loopback origins keep Secure cookies;
set `DASHBOARD_URL` to the public HTTPS origin when using a reverse proxy.

## Local Memory Profile

The default Docker Compose stack uses Postgres/pgvector. For a heavier local
memory stack with Qdrant, Neo4j, and local HuggingFace reranking, opt into the
`local-memory` profile and select the matching providers through env vars:

After configuring these values in `server/.env`, the normal start command is:

```bash
cd server
make local-up
```

If Docker reports `network ... not found`, its retained containers refer to an
obsolete Compose network. Recreate the project containers and network with:

```bash
make local-recover
```

This preserves the named PostgreSQL, Qdrant, Neo4j, and HuggingFace cache
volumes. It does not run `docker compose down -v`; `make clean` remains the
explicit destructive cleanup command.

The equivalent one-off configuration is:

```bash
cd server
MEM0_VECTOR_STORE_PROVIDER=qdrant \
MEM0_GRAPH_STORE_PROVIDER=neo4j \
MEM0_RERANKER_PROVIDER=huggingface \
OPENAI_BASE_URL=http://host.docker.internal:8080/v1 \
OPENAI_API_KEY=local \
NEO4J_PASSWORD='local-neo4j-password' \
docker compose --profile local-memory up --build
```

This starts Qdrant on `localhost:6333` and Neo4j on `localhost:7475`/`7688`.
HuggingFace model files are cached in the `huggingface_cache` Docker volume.

An available model is not necessarily a loadable model: a local provider can
reject extraction with HTTP 507 when its memory allowance is too small. Mem0
reports that upstream failure as HTTP 502 (`provider_unavailable`); starting
the provider alone does not establish inference readiness. Select a model that
fits the workstation, then verify an inferred write and readback. To change an
existing deployment without restarting it, an authenticated admin can POST
`{"llm":{"config":{"model":"Ministral-3-8B-Instruct-2512-4bit"}}}` to
`/configure`. This is an example installed 8B model, not a bundled dependency.
The override persists in the application database and takes precedence over
`MEM0_DEFAULT_LLM_MODEL` on restart; `GET /configure` shows the effective model.
Do not change embedding models/dimensions against existing vectors as part of
an extraction-model change.

Selecting `MEM0_GRAPH_STORE_PROVIDER=neo4j` enables the SDK's relationship graph:
canonical vector memories remain the source of truth, successful writes are
projected asynchronously to Neo4j, and matching graph evidence reranks only the
bounded candidates returned by semantic search. The server also accepts the
dedicated `MEM0_GRAPH_NEO4J_URI`, `MEM0_GRAPH_NEO4J_USERNAME`,
`MEM0_GRAPH_NEO4J_PASSWORD`, and `MEM0_GRAPH_NEO4J_DATABASE` variables; these
take precedence over the shorter `NEO4J_*` Compose settings. The Docker setup
uses a five-second Neo4j transaction timeout by default; override it with
`MEM0_GRAPH_QUERY_TIMEOUT_SECONDS`.

Verify the live Neo4j projection and retrieval lifecycle without consuming LLM
tokens:

```bash
docker compose exec mem0 python scripts/smoke_relationship_graph.py
```

The smoke test checks idempotent projection, exact-scope isolation, semantic
candidate bounding, relationship replacement, and deletion, then removes its
temporary graph collection.

## Local Coding-Memory Pilot (Separate Compose Project)

This optional, host-local pilot is separate from the standard development stack
above and from the synthetic assessment/evaluation stack. It uses only the
`mem0`, `postgres`, and `qdrant` services in `compose.pilot.yaml`; it does not
start a dashboard, graph store, reranker, assessment runner, capture hook, or
bootstrap flow. Its Compose project is `mem0-coding-pilot`, with the API bound
only to `127.0.0.1:8899`. It does not alter the existing evaluation stack on
port `8898`.

### Prerequisites and workstation readiness

- Docker Desktop with Compose v2, and the pilot's exact ARM64 CPU dependency
  image already present locally:
  `fixture-cpu-build-only-mem0@sha256:53c6ae836fc6b45bc80779a034082ab23fe728832735f27a614dd7e7f842ffab`.
  Compose uses `pull_policy: never`: this fixture is not a published image and
  the pilot does not build or download images. On another machine, provision
  this image through an authorized local transfer/build process and verify it
  first; do not assume a registry pull is available or substitute a different
  image without validating it.
- The pinned Postgres and Qdrant images must also be available locally; no
  database/vector ports are published:
  `postgres@sha256:ae69c452f483507a6b99fb654cf93aad7fe156ffd2c56247707eef4e36d3c12b`
  and
  `qdrant/qdrant@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`.
- An absolute host directory of already-downloaded FastEmbed sparse-model
  assets, set as `MEM0_PILOT_FASTEMBED_CACHE`. The directory is mounted
  read-only at `/pilot-assets/fastembed-cache`; `FASTEMBED_CACHE_PATH` points
  there and both `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` prevent model
  downloads. The pilot has no first-run asset bootstrap.
- The local oMLX OpenAI-compatible endpoint must be running at
  `http://host.docker.internal:8080/v1` with the configured models available.
  Langfuse must be reachable at `http://host.docker.internal:4000`. Neither is
  started or stopped by this Compose project.

The prepared workstation already has a private `server/.env.pilot`; preserve
it when restarting. On a new workstation, copy `server/.env.pilot.example` to
`server/.env.pilot` and fill in the required values: `MEM0_PILOT_API_KEY`, `MEM0_PILOT_JWT_SECRET`,
`MEM0_PILOT_POSTGRES_PASSWORD`, `OMLX_API_KEY`, `LANGFUSE_PUBLIC_KEY`, and
`LANGFUSE_SECRET_KEY`. The API key and JWT secret must be strong random
secrets. Keep the private file ignored and local. Never print it, paste its
contents into logs/chat, or expose secrets via `docker inspect` environment
output or `docker compose config`; the Compose file fails fast when required
interpolation values are missing (`docker compose config --quiet` is safe for
validation).

The dedicated profile pins `Ministral-3-8B-Instruct-2512-4bit` for extraction
and `Qwen3-Embedding-0.6B-4bit-DWQ` for embeddings (1024 dimensions). Extraction
uses temperature 0, top-p 1, 2048 max tokens, 120-second timeout, zero retries,
disabled thinking, and the strict `memory_extraction` JSON schema. The complete
`extra_body` schema is embedded in the Compose configuration; startup does not
depend on the ignored assessment `freeze.json`. Langfuse tracing is enabled
with metadata-only standard tracing; raw request/response capture is not
enabled. Telemetry is disabled.

### Start, inspect, stop, and resume

Run these commands from the repository root. `up --wait` waits for healthy
dependencies and the authenticated API health check:

The health check proves API startup and authentication, not oMLX availability.
Start oMLX separately before adding or searching memories. If port `8080` is
not listening, those operations can return `502` even while the API is healthy.

```bash
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml \
  up -d --wait --wait-timeout 180
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml ps
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml logs -f mem0
```

Stop containers without deleting data:

```bash
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml stop
```

Start again with the same `up` command. `down` also preserves named volumes;
never use `down -v` for this pilot:

```bash
docker compose --env-file server/.env.pilot -f server/compose.pilot.yaml down
```

Persistent state is isolated in named volumes for the Mem0 history database
(`history`, at `/pilot-history/history.db`), the server app/auth/config database
(`app_db`), and Qdrant memories (`memories`). `server/init-db.sh` is mounted
read-only and initializes the fresh database. On startup the service runs
`alembic upgrade head` followed by `uvicorn main:app --host 0.0.0.0 --port
8000` without reload. The repo's `../mem0` package is mounted read-only at
`/usr/local/lib/python3.12/site-packages/mem0`; `main.py`, `server_state.py`,
`runtime_tracing.py`, `errors.py`, `compat.py`, and the platform/entity routers
are mounted read-only into their `/app` paths. The entire server directory and secrets are not
mounted. Resource limits are 1 GiB/1 CPU for Mem0, 256 MiB/0.5 CPU for Postgres,
and 512 MiB/0.5 CPU for Qdrant; services restart unless stopped.

### Hook connection and configuration

Compose does not install coding-harness hooks. The separately verified OMP
source adapter and its setup are documented in [the interface catalog](../design/coding-memory-interfaces.md).
For other local clients, point them at `http://127.0.0.1:8899` and pass `X-API-Key` using the private
`MEM0_PILOT_API_KEY` value. This key is admin-capable; give it only to trusted
local hooks. Keep stable `user_id` and project `app_id` scopes across writes
and searches to separate retrieval contexts. These filters are not tenant
authorization. The authenticated API
includes `POST /memories` for add, `POST /search` for semantic search,
`GET /memories` for scoped listing, and standard memory get/update/delete
routes. `GET /configure` returns redacted effective configuration;
`POST /configure` requires admin authentication. OpenAPI is available at
`/docs` while the service is running. Auth is enabled; do not disable it.

Configuration changes submitted through `/configure` persist in the `app_db`
volume and are reapplied after restart, layered over the Compose-provided
defaults. Preserve that volume if changes should survive; the selected
assessment profile itself is embedded in the Compose file.

### Advisory limitation

This local profile is useful for advisory retrieval of repository facts and
decisions, not authoritative exact-identifier recall. Prior reviewed assessment
evidence found one misspelled stored path conflicting with correct evidence on
two path queries. Verify paths and other exact identifiers against the
repository. The bounded harness integration checks are recorded separately in
[the interface catalog](../design/coding-memory-interfaces.md); they are not
benchmark scores or a guarantee of exact-identifier recall.

## Local CLI and MCP Compatibility

The self-hosted server exposes a Platform-compatible adapter for local tools:

- CLI auth accepts `Authorization: Token <api-key>` in addition to `X-API-Key`.
- CLI routes are available under `/v1/...` and `/v3/...`.
- The MCP endpoint is available at `/mcp` with hosted-compatible tool names.
- `app_id` is preserved as project scope for coding-agent integrations.

Point the unchanged Node CLI at localhost:

```bash
MEM0_BASE_URL=http://localhost:8888 MEM0_API_KEY='<api-key>' mem0 status
MEM0_BASE_URL=http://localhost:8888 MEM0_API_KEY='<api-key>' mem0 add 'I prefer local memory' --user-id local-test --app-id mem0-local
MEM0_BASE_URL=http://localhost:8888 MEM0_API_KEY='<api-key>' mem0 search 'local memory' --user-id local-test --app-id mem0-local
```

Point the shared agent-plugin runtime at localhost. `MEM0_API_URL` is the current
plugin setting; this downstream branch also accepts `MEM0_BASE_URL` as a
backward-compatible alias used by the CLIs:

```bash
export MEM0_API_URL=http://localhost:8888
export MEM0_API_KEY='<api-key>'
```

The current native plugins run their own stdio MCP server, so a separate
`MEM0_MCP_URL` is no longer required.

Hosted Platform-only request fields such as `rerank`, `keyword_search`,
`fields`, `categories`, `immutable`, and `source` are accepted for local
compatibility. Fields without an OSS equivalent are treated as no-ops.

## Dashboard

Once logged in, the dashboard exposes:

- **Requests** — live audit log of API calls (method, path, status, latency).
- **Memories** — browse memories, filter by user ID.
- **Entities** — list every `user_id`, `agent_id`, `app_id`, and `run_id` that owns memories, with counts. Delete an entity to cascade-delete its memories.
- **API Keys** — create, label, and revoke per-user keys.
- **Configuration** — runtime LLM and embedder override. Changes persist to the app database and reapply on restart, layered over the values from your `.env`.
- **Settings** — account profile and password.

## Telemetry

Enabled by default, matching the Mem0 OSS library. Sends at most two events per install to the same anonymous PostHog project the library uses:

- `admin_registered` — fired when the first admin is created (wizard or direct API call). Properties: email domain, server version, install UUID.
- `onboarding_completed` — fired when the setup wizard reaches its final success state. Carries the same properties plus the freeform `use_case` the operator entered. API-only bootstraps never emit this event.

Set `MEM0_TELEMETRY=false` to opt out.

## Security headers

The dashboard sets the following response headers on every path (see `server/dashboard/next.config.mjs`):

- `X-Frame-Options: DENY`
- `Content-Security-Policy: frame-ancestors 'none'`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`

Together these prevent iframe embedding, sniffing of mislabelled MIME types, and cross-origin referrer leaks. Harden further behind your own reverse proxy if needed.

## Migrating from `ankane/pgvector` to `pgvector/pgvector`

The `ankane/pgvector` Docker image is archived and no longer maintained. This release
replaces it with the official `pgvector/pgvector:pg17` image (PostgreSQL 17, pgvector 0.8.0).

**What changed:**

| | Before | After |
|---|---|---|
| Docker image | `ankane/pgvector:v0.5.1` | `pgvector/pgvector:pg17` |
| PostgreSQL version | 15 | 17 |
| pgvector version | 0.5.1 | 0.8.0 |
| Credentials | Hardcoded `postgres`/`postgres` | Driven by `POSTGRES_USER` / `POSTGRES_PASSWORD` env vars |

### Fresh installs (no existing data)

No migration needed. Copy `.env.example` to `.env`, set `POSTGRES_PASSWORD`, and run:

```bash
cd server
make up
```

### Existing installs (preserving data)

PostgreSQL 17 cannot read data files written by PostgreSQL 15 directly.
You must export your data first, then import it into the new container.

**1. Export your data from the old container**

With the old stack still running:

```bash
cd server

# Dump all databases (mem0 memories + mem0_app auth/config data)
docker compose exec -T postgres pg_dumpall -U postgres > mem0_backup.sql
```

Verify the dump file is non-empty:

```bash
ls -lh mem0_backup.sql
```

**2. Stop the old stack and remove the old volume**

```bash
# Stop containers
docker compose down

# Remove the old Postgres data volume
docker compose down -v
```

> **Warning:** `docker compose down -v` deletes the `postgres_db` volume permanently.
> Only run this after you have verified your backup.

**3. Update your `.env`**

The Postgres credentials are no longer hardcoded in `docker-compose.yaml`.
Add them to your `.env` file (or verify they match your old setup):

```bash
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<your-password>    # required — compose will refuse to start without it
POSTGRES_COLLECTION_NAME=memories
```

If you previously relied on the hardcoded defaults (`postgres`/`postgres`), set
`POSTGRES_PASSWORD=postgres` to keep the same credentials.

**4. Start only Postgres**

Start **only** the Postgres container first — do not start the mem0 API yet.
The API runs `alembic upgrade head` on startup, which creates empty tables that
would conflict with the restore.

```bash
docker compose up -d postgres
```

Wait for the Postgres healthcheck to pass:

```bash
docker compose exec -T postgres pg_isready -q && echo "ready" || echo "not ready"
```

**5. Restore your data**

```bash
docker compose exec -T postgres psql -U postgres < mem0_backup.sql
```

You may see notices like `role "postgres" already exists` — these are harmless.

> **Important:** You must restore before starting the mem0 API container. The API
> runs database migrations on startup which create empty tables — restoring after
> that would fail with duplicate-key errors and lose your API keys and settings.

**6. Start the API**

Now start the mem0 API container. Alembic will detect the existing tables and
only apply any new migrations:

```bash
docker compose up -d mem0
```

**7. Verify**

```bash
# Check the API is healthy
make health

# Confirm your memories are present
curl -s http://localhost:8888/memories?user_id=<your-user-id> -H "X-API-Key: <your-api-key>"
```

### Rollback

If you need to revert, restore the old image tag in `docker-compose.yaml`:

```yaml
postgres:
    image: ankane/pgvector:v0.5.1
```

Then `docker compose down -v`, `docker compose up -d --build`, and restore from
`mem0_backup.sql` into the old container the same way.

## Reference

Additional product and API documentation lives at [docs.mem0.ai](https://docs.mem0.ai/open-source/overview).
