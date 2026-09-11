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
