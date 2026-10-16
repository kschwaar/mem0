# Langfuse evaluation tracing

The TypeScript evaluator exports OpenTelemetry spans directly to local Langfuse
and delivers grades separately through the Scores API, retaining its scheduler
and local ledger. Its default `--trace-backend none` needs no Langfuse setup.
The SDK/server/browser runtime instrumentation is separate, described below.
Native Langfuse experiments are not implemented by these evaluation CLIs.

## Runtime inference callsite inventory

Runtime tracing is opt-in. Supply `MEM0_RUNTIME_TRACING=true` and local project
keys privately to Python/Node processes. Browser examples use a loopback collector
without project keys. The server and deployment guide is
[`server/RUNTIME_TRACING.md`](../../server/RUNTIME_TRACING.md).
Do not combine the evaluator's own `--trace-backend langfuse` with automatic SDK
runtime export in the same standalone CLI; use `MEM0_RUNTIME_TRACING=false` there.

| Owned location | AI/inference boundary and coverage |
| --- | --- |
| `mem0/llms/` | `LLMBase.generate_response` wraps all 18 concrete adapters: Anthropic, Bedrock, Azure OpenAI/structured, DeepSeek, Gemini, Groq, LangChain, LiteLLM, LM Studio, MiniMax, Ollama, OpenAI/structured, Sarvam, Together, vLLM, xAI. |
| `mem0/embeddings/` | `EmbeddingBase.embed`/`embed_batch` cover Bedrock, Azure, FastEmbed, Google GenAI, Hugging Face, LangChain, LM Studio, Ollama, OpenAI, Together, Vertex. `MockEmbeddings` is synthetic, not evidence of a real model request. |
| `mem0/reranker/` | `BaseReranker.rerank` covers Cohere, Hugging Face, LLM, SentenceTransformer and ZeroEntropy, including local `predict` inference. LLM reranking has a nested LLM generation. |
| `mem0/graphs/extractors.py` | Structured graph extraction calls its supplied LLM; Mem0 wrappers are reused, and custom protocol implementations get an explicit generation boundary. |
| `mem0/memory/utils.py` | `get_image_description` has a utility span; its LLM invocation has a generation span. |
| `mem0/vector_stores/qdrant.py` | FastEmbed BM25 `_encode_bm25` is a local sparse-inference span. |
| `mem0/vector_stores/vertex_ai_vector_search.py` | Internal `embed_query`/`embed_documents` calls are observed. Other stores accept vectors and are not model inference. |
| `mem0/memory/main.py` | Sync/async memory operations use those boundaries; entity-search thread submissions propagate request context. Memory CRUD itself is not relabeled as inference. |
| `mem0-ts/src/oss/src/llms/` | Direct constructors and factories cover all 18 adapters: Anthropic, Bedrock, Azure, DeepSeek, Google, Groq, LangChain, LiteLLM, LM Studio, MiniMax, Mistral, Ollama, OpenAI/structured, Sarvam, Together, vLLM, xAI. |
| `mem0-ts/src/oss/src/embeddings/` | All 11 adapters: Bedrock, Azure, FastEmbed, Google, Hugging Face, LangChain, LM Studio, Ollama, OpenAI, Together, Vertex. |
| `mem0-ts/src/oss/src/rerankers/` | Cohere, cross-encoder, LLM and ZeroEntropy; local inference is covered even without a recognizable raw SDK client. |
| `mem0-ts/src/oss/src/utils/runtime_tracing.ts` | Recognized raw chat/Responses/embedding/messages/Google/Ollama/Mistral/rerank/Vertex/LangChain methods enrich active adapter spans; standalone clients can be explicitly instrumented. Returned provider streams finish on consumption/error/cancellation. |
| `server/main.py` | Startup configures the SDK exporter; `mem0.http` roots associate model/embedding/sparse spans with the generated request ID. Shutdown owns flush. |
| `integrations/vercel-ai-sdk/src/provider-response-provider.ts` | One native provider `doGenerate`/`doStream` observation; the generic Mem0 layer delegates without a second generation. Stream consumption/error/cancellation owns completion. Host application supplies OTEL export. |
| `examples/notebooks/customer-support-chatbot.ipynb` | Anthropic `messages.create` in `SupportChatbot.handle_customer_query`. |
| `examples/graph-db-demo/kuzu-example.ipynb` | OpenAI `chat.completions.create` in `chat_with_memories`. |
| `examples/misc/movie_recommendation_grok3.py` | xAI via OpenAI-compatible chat completions. |
| `examples/misc/voice_assistant_elevenlabs.py` | Whisper transcription; ElevenLabs speech generation plus full playback; CrewAI kickoff dispatch. |
| `examples/misc/fitness_checker.py`, `personal_assistant_agno.py`, `diet_assistant_voice_cartesia.py` | Full Agno run/print-response operations, including image input and Cartesia tool dispatch, without exporting content. |
| `examples/misc/personalized_search.py` | LangChain `AgentExecutor.invoke` logical run. |
| `examples/misc/test.py`, `study_buddy.py` | OpenAI Agents `Runner.run_sync` / awaited `Runner.run`, including handoffs. |
| `examples/misc/healthcare_assistant_google_adk.py` | ADK `Runner.run_async`, consumed through final events inside the boundary. |
| `examples/multiagents/llamaindex_learning_system.py` | Awaited LlamaIndex `AgentWorkflow.run`. |
| `examples/misc/strands_agent_aws_elasticache_neptune.py` | Strands agent invocation, with its complete logical operation observed; hidden Bedrock requests are not separately counted. |
| `examples/notebooks/mem0-autogen.ipynb` | AutoGen `initiate_chat` conversations in code cells 2/5/8/11. |
| `examples/multimodal-demo/src/hooks/useChat.ts`, `examples/multimodal-demo/useChat.ts` | Direct OpenAI multimodal stream creation and full iteration. Existing API settings expose explicit collector opt-in. |
| `examples/yt-assistant-chrome/src/background.js` | Chat Completions REST fetch through consumed response stream. Model-list credential validation is not inference. Webpack's background bundle is the manifest service worker. |
| `examples/openai-inbuilt-tools/index.js` | Explicitly instrumented raw OpenAI `responses.create`, including built-in/function tool dispatch. Uses this checkout's standalone tracing export. |
| `examples/mem0-demo/app/api/chat/route.ts` | Raw AI SDK OpenAI model is explicitly wrapped at its `doStream` boundary before `streamText`. |
| `examples/vercel-ai-sdk-chat-app/src/hooks/useChat.ts` | Browser AI SDK stream creation and text-stream consumption, exporting through the loopback collector. |
| `scripts/evaluate_ai_calls.py`, `scripts/evaluate_memory_trajectory.py` | Real LLM/embedder/reranker adapter calls use SDK instrumentation; offline/mock results are not proof of model inference. |
| `mem0-ts/scripts/evaluate_ai_calls.ts` | Its evaluation-local exporter captures SDK calls and grades; use that tracing backend rather than also enabling automatic runtime export. |
| Frozen `artifacts/evaluation/**` benchmark/scoring sources | Raw OpenAI/Anthropic answerer/judge clients require the opt-in `scripts/trace_python.py` launcher. Archived sources/fingerprints remain unchanged. |

### Non-owned and non-inference boundaries

Hosted `MemoryClient`/CLI operations and editor/agent plugins call Mem0 HTTP APIs
or host-supplied runtimes. Their remote model internals cannot be instrumented
from a client checkout. Local OSS CLI/plugin calls inherit SDK/service coverage.
Graph/vector database operations, supplied-vector storage, model lists, credential
checks, local file/audio writes and mocked tests are not independent AI calls.
The graph demonstration notebooks other than Kuzu use the shared Mem0 providers.

Framework spans identify complete logical dispatches, not one physical request:
tools, handoffs and private retries may contain additional work. Explicit Python
setup instruments supported raw OpenAI, Anthropic and Google GenAI methods too;
unrecognized host/vendor methods remain opaque inside the dispatch. Arbitrary
custom adapters need an explicit `trace_call` or `withRuntimeSpan` boundary.
Subprocesses need their own tracing setup. `scripts/trace_python.py` propagates an
opt-in startup hook through inherited `PYTHONPATH`, preserving existing startup
hooks; children that replace their environment or use an interpreter without
this checkout's tracing dependencies require explicit setup.

### Real local readback

The local audit authenticated project `local-project` on Langfuse 4.53.0 and queried
the actual generation/embedding observations after HTTP, raw async Python, Python
stream, built Node, Vercel stream and browser-to-collector inference. Trace IDs and
deployment settings are recorded in the server guide. Sanitized evidence is under
ignored `artifacts/evaluation/otel-audit-20261012/`.

For independent readback, set an ISO-8601 time window and the observed trace ID;
keys must already be privately supplied:

```bash
curl --fail --silent --show-error \
  --user "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  --get http://localhost:4000/api/public/v2/observations \
  --data-urlencode "fromStartTime=$FROM_START_TIME" \
  --data-urlencode "toStartTime=$TO_START_TIME" \
  --data-urlencode 'limit=100' \
  --data-urlencode 'fields=core,basic,metadata,usage' \
  | jq --arg trace "$TRACE_ID" '.data[] | select(.traceId == $trace)'
```

Check parent IDs and observation types, not just an HTTP export acknowledgement.
Reported usage must come from the provider; missing usage is unknown.
Custom local models can have no Langfuse pricing/model ID while the model-name
metadata and available usage are present. Remote vendor/framework runs and a
loaded Chrome extension were not established by the local inference smoke.

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
The package's `pnpm test` launcher supplies the Node VM option required by
the OTel exporter's dynamic HTTP import inside Jest. The integration test uses
actual OTLP HTTP JSON export and native Scores API fetch against an ephemeral
loopback receiver with fake credentials. For an isolated run that avoids the
package's dotenv setup entirely:

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
