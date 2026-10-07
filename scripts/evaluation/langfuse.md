# Langfuse evaluation tracing

The TypeScript evaluator can export real OpenTelemetry spans directly to
Langfuse and deliver grades separately through the Scores API. It keeps the
existing scheduler and local ledger. The default `--trace-backend none` requires
no Langfuse configuration. Python tracing, native Langfuse experiments and a
collector are not implemented by this change.

## Local readiness and credentials

The coordinator confirmed `http://localhost:4000/api/public/health` was healthy,
version **4.53.0**, on 2026-10-06. Recheck health before running. Health does not
prove project authentication or ingestion. Create project API keys in the local
Langfuse project settings and provide them to the coordinator's process through
its environment. The scripts read only these explicit environment variables:

- `LANGFUSE_BASE_URL=http://localhost:4000`
- `LANGFUSE_PUBLIC_KEY`: the project's public API key
- `LANGFUSE_SECRET_KEY`: the same project's secret API key

No `.env` or credential file is loaded automatically by these CLIs. Missing variables fail
setup; there is no automatic cloud endpoint. Keep keys out of command history,
output directories and git. The validation agent uses fake keys with a fake
receiver; only the coordinator executes the authenticated synthetic smoke once
credentials have been supplied.

From `mem0-ts/`, with those variables already supplied:

To load the repository root `.env` explicitly, prefix any command below with
`DOTENV_CONFIG_PATH=../.env NODE_OPTIONS='-r dotenv/config'`. Set all three
Langfuse variables in that file, including `LANGFUSE_BASE_URL=http://localhost:4000`.
This loads keys into the process without placing them in command history.

```bash
corepack pnpm exec ts-node --compiler-options '{"module":"CommonJS","rootDir":"."}' \
  scripts/evaluate_ai_calls.ts --offline \
  --cases ../scripts/evaluation/cases.jsonl \
  --out /tmp/mem0-langfuse-offline-unique \
  --repeats 3 --concurrency 2 --trace-backend langfuse
```

Use a new output directory. Offline traces have `mode=offline_synthetic`, no
actual SDK generation/embedding observations, and disabled model quality gates.
For real inference, remove `--offline` and add
`--config /absolute/path/to/local-config.json`; validate model readiness as in
the [workbench guide](README.md). `--task`, `--case`, `--min-pass-rate` and
`--max-p95-ms` retain their existing evaluator semantics. Use `--help` to inspect
all flags.

## Observation tree and grades

Each trial has its own real OTel trace and root observation. Adapter operations,
actual observed SDK generation/embedding calls, and the local grader are nested
beneath it. The ledger stores the 32-character hex `trace_id` and 16-character
hex `root_observation_id`; SDK completion IDs are not trace IDs. All spans carry
run/case/repetition/mode metadata for filtering concurrent trials. Unsupported
cases are skipped; synthetic replay never fabricates model calls.

Only SDK observations carry usage reported by the SDK response, captured before
adapter normalization. Parent spans do not repeat token usage. Missing tokens,
unobserved SDK calls and hidden HTTP retries remain unknown in the local ledger.
The default records hashes, with no prompt or response content. `--record-content`
opts into bounded content recording; consider the sensitivity of the selected
fixtures before using it. Raw embeddings are not intended for export.

Grades attach to the actual root observation using both `traceId` and
`observationId`. `trial_status` is CATEGORICAL. `contract_pass` is BOOLEAN 1/0 for
pass/fail outcomes, omitted for skipped/error outcomes without grading evidence.
`fallback_detected` is BOOLEAN only when known, omitted for unknown/skipped
outcomes. Synthetic grades are explicitly marked `offline_synthetic`; they
establish fixture/transport behavior, not model quality.

`calls.jsonl` stays authoritative. Trace export failures do not change inference
outcomes, local grades, quality gates or evaluator exit status. Inspect
`summary.json`'s `observability` separately: accepted, failed and unknown spans
are distinct, and pending score deliveries remain in `scores-outbox.jsonl`.
An accepted HTTP export is not proof that a query has read the observation back.
The exporter queue is bounded; unknown counts can include drops or unresolved
flushes. Scores are queued durably after the local outcome, then delivered with
bounded retries after workers finish. Replay uses stable score IDs, preserving
deduplication after ambiguous network failures.

Retry pending scores without inference:

```bash
corepack pnpm exec ts-node --compiler-options '{"module":"CommonJS","rootDir":"."}' \
  scripts/replay_evaluation_scores.ts --output-dir /tmp/mem0-langfuse-offline-unique
```

The replay CLI exits nonzero while scores remain pending. Each flush is bounded
to 100 scores and 30 seconds; repeat replay for larger outboxes. `delivered` counts
acknowledgements during that flush; `queued` counts all journaled scores.
Credentials are resolved afresh
from the environment and never persisted in the journal.

## Synthetic authenticated smoke

The coordinator runs this helper only after receiving project credentials:

```bash
corepack pnpm exec ts-node --compiler-options '{"module":"CommonJS","rootDir":"."}' \
  scripts/smoke_langfuse.ts --output-dir /tmp/mem0-langfuse-smoke-unique \
  --verify-timeout-ms 120000
```

This makes **zero model calls**. It injects success, malformed schema and
fallback scenarios with explicit synthetic evidence. It exports three roots,
three fixture children and three grader children, then nine attached scores.
It creates no generation/embedding observation and claims no real token usage.
`synthetic-evidence.json` records the injected scenarios; `smoke-report.json`
records export/delivery counts and verified readback. A successful smoke exits
zero only after verifying all nine observation rows and nine scores with the
expected physical parents, synthetic metadata, types, values and attach IDs.

The helper reads `GET /api/public/v2/observations` as flat observation rows and
`GET /api/public/v3/scores` with the `subject` field group. Scores v3 returns
BOOLEAN values as `true`/`false`, while score creation takes numeric 1/0. The
OTLP exporter sends `x-langfuse-ingestion-version: 4`; without it, observation
reads may lag. Score reads can also lag after an accepted write. Polling is
bounded; an expired readback budget is incomplete validation, not a passed
smoke. The timeout accepts 1000–600000 ms. Use the local instance's unauthenticated
`/api/openapi.yaml` or `/api/docs` to confirm schemas if its version differs.

Primary references: [OTel mappings](https://langfuse.com/integrations/native/opentelemetry),
[v4 custom ingestion](https://langfuse.com/integrations/native/opentelemetry/migration-to-v4),
[Public API and current read endpoints](https://langfuse.com/docs/api-and-data-platform/features/public-api),
and [OpenAPI schema](https://api.reference.langfuse.com/).

## Fake receiver checks

Run the focused evaluator, tracing, score and integration tests using pnpm.
The integration test uses actual OTLP HTTP JSON export and native Scores API
fetch against an ephemeral loopback receiver with fake credentials. Its isolated
Jest configuration avoids the package's dotenv setup entirely. The Node VM
option supports the OTel exporter's dynamic HTTP import inside Jest:

```bash
NODE_OPTIONS=--experimental-vm-modules corepack pnpm exec jest --runInBand \
  --config='{"preset":"ts-jest","testEnvironment":"node","transform":{"^.+\\.tsx?$": ["ts-jest", {"tsconfig":"tsconfig.test.json"}]}}' \
  --runTestsByPath tests/evaluation_langfuse.integration.test.ts
```

The package currently has unrelated baseline TypeScript failures. Check new
scripts/tests separately and report package failures separately from new ones:

```bash
corepack pnpm exec tsc --noEmit --strict --skipLibCheck --esModuleInterop \
  --target ES2022 --module commonjs --moduleResolution node \
  scripts/evaluation_tracing.ts scripts/evaluation_scores.ts \
  scripts/replay_evaluation_scores.ts scripts/smoke_langfuse.ts \
  tests/evaluation_langfuse.integration.test.ts
corepack pnpm exec tsc --noEmit
corepack pnpm exec prettier --check scripts/smoke_langfuse.ts \
  tests/evaluation_langfuse.integration.test.ts ../scripts/evaluation/langfuse.md
```
