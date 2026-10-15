# Optional runtime tracing to local Langfuse

Tracing is disabled by default. Opt-in instrumentation covers Python and Node
provider boundaries and browser streaming operations. Export acknowledgement is
not delivery proof: query Langfuse observations after a real inference call.

## Python service and SDK

The server configures `mem0.runtime_tracing.configure_tracing(os.environ)` before
constructing `Memory`. HTTP middleware creates a request-root span; SDK inference
spans inherit it, including context-propagating asynchronous/threaded work.
`server/runtime_tracing.py` owns HTTP-root context and status only. Startup failure
and FastAPI shutdown flush and shut down the SDK-owned exporter.

Provide these settings through the process environment or an ignored env file:

```text
MEM0_RUNTIME_TRACING=true
LANGFUSE_BASE_URL=http://host.docker.internal:4000
LANGFUSE_PUBLIC_KEY=<project public key>
LANGFUSE_SECRET_KEY=<project secret key>
```

Use `http://localhost:4000` for host processes. Docker uses
`http://host.docker.internal:4000`. Only allowed local/Docker hostnames and a root
base URL are accepted; credentials, query strings and fragments in URLs are
rejected. Install this checkout's `tracing` extra for standalone Python use.
The server images install `requirements-tracing.txt`.

`configure_tracing()` is explicit setup; lazy setup is best effort when
`MEM0_RUNTIME_TRACING=true`. `trace_call(name, role, kind)` wraps custom inference.
`flush_tracing()` detaches, flushes and shuts down the owned runtime, permitting
fresh configuration. An application-supplied tracer is not shut down by the SDK.
Python does not replace the application's global OTEL provider.

Configuration also instruments supported raw OpenAI/OpenAI-compatible,
Anthropic and Google GenAI SDK methods. Nested SDK calls enrich the active Mem0
provider observation instead of adding a duplicate generation. Decorated async
SDK methods are awaited. OpenAI `Stream`/`AsyncStream` retain their original
object/type and defer span completion until iteration finishes, fails or closes.
Consumers must close abandoned streams. That stream hook is intentionally
restricted to OpenAI's known `_iterator` implementation.

Provider spans carry operation/class/model, reported token usage, elapsed time,
outcome and exception type. Prompts, outputs, image/audio content, vectors,
credentials and exception messages are not attached. Missing usage is unknown,
not zero; cached/reasoning subcounts overlap their parent counts. Spans describe
logical adapter/SDK invocations, not individual SDK transport retries.

The Python exporter has a 256-span queue, 32-span batches and a five-second
export timeout. Shutdown/flush cannot guarantee delivery after abrupt termination.

## Node and Vercel

Build the current `mem0-ts` checkout before using its new `mem0ai/tracing` export.
`configureRuntimeTracing`, `flushRuntimeTracing`, `shutdownRuntimeTracing`,
`traceRuntimeObject` and `instrumentRuntimeClient` are available there and from
`mem0ai/oss`. The SDK's OSS and standalone bundles share one process-local runtime.
Provider constructors cover direct imports as well as factory-created instances.
An existing application-owned global OTEL provider/context manager is preserved.

`integrations/vercel-ai-sdk` observes the native provider delegate once, rather
than both Mem0 wrapper layers. It uses the application's OTEL provider; it does
not configure a Node exporter inside browser bundles. Recorded streams own their
span through consumption, error and cancellation, retaining other response fields.
Node applications can configure the SDK exporter before invoking that integration.
Never put Langfuse project keys in frontend environment variables.

## Browser collector

`examples/browser_tracing.js` exports OTLP/HTTP JSON to an explicitly enabled
loopback endpoint, defaulting to `http://localhost:4318/v1/traces`. Multimodal and
Chrome assistant settings expose the opt-in; the Vercel browser chat example uses
`VITE_MEM0_RUNTIME_TRACING=true` and `VITE_MEM0_OTLP_ENDPOINT`.
Browser boundaries include full stream consumption, not just response headers.
They export configured provider/model and outcome; token usage may be unavailable.

The `tracing` Compose profile includes the pinned collector in
`server/otel-collector.yaml`. It binds host port 4318 to loopback only, accepts
local/extension origins and forwards an allowlist of metadata to local Langfuse.
Project credentials remain in the collector:

```bash
# Project keys must already be supplied privately in this shell.
export LANGFUSE_OTLP_AUTH="$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')"
docker compose --profile tracing up -d otel-collector
```

The normal server Compose environment still requires its existing database
settings. Do not launch a second collector on an already occupied port.
The collector is a trusted local development endpoint, not an authenticated
public gateway. Never expose it to a LAN or the internet.

## External benchmark scripts

Frozen benchmark checkouts/prepared scoring artifacts are not rewritten.
With this checkout installed and the tracing environment supplied, run their
answerer/judge entry point through:

```bash
python scripts/trace_python.py /absolute/path/to/existing/script.py [args...]
python scripts/trace_python.py --module existing.module [args...]
```

The launcher preserves script arguments/import roots and flushes on exit.
It temporarily adds an opt-in startup hook to inherited `PYTHONPATH`, so Python
subprocesses that preserve the environment are instrumented too. Existing
`sitecustomize` hooks, including resource guards, run before tracing setup.
Each child interpreter still needs this checkout's SDK/tracing dependencies;
children that replace their environment must receive the bootstrap/settings
explicitly. Parent argv, paths and bootstrap environment are restored on exit.
Archived source and fingerprints remain unchanged.
The TypeScript evaluator has its own `--trace-backend langfuse` exporter/ledger;
run that standalone CLI with `MEM0_RUNTIME_TRACING=false` to avoid running two
independent tracing systems around the same inference.

## Coverage and verification

The owned callsite inventory is in `scripts/evaluation/langfuse.md`.
Hosted Mem0 requests and editor/agent-host inference internals cannot expose their
remote server spans from this checkout. Framework dispatch spans cover complete
runs; raw SDK observations exist only for recognized SDK methods. Arbitrary
custom providers require `trace_call`/`withRuntimeSpan`. Remote vendor credentials,
framework services and Chrome extension runtime are not implied by a local smoke.

The local audit queried authenticated `GET /api/public/v2/observations` from
Langfuse 4.53.0, project `local-project`, after real inference:

| Path | Queried trace ID |
| --- | --- |
| Reloaded HTTP instruction generation | `ca5fa10a5b3711443c55b7c7464f59ad` |
| Reloaded HTTP search embedding + BM25 | `2e9f979797e8ce55a0ee836f67826eb4` |
| Raw asynchronous OpenAI call through benchmark launcher | `fe2e9e5107adae5e99ef62a5f3c4c9a9` |
| Fully consumed raw Python OpenAI stream | `bdcfa0ab544c363ab79a3617c2fb3c2c` |
| Built Node SDK generation + embedding | `e366c5eedc74aa890f116460e1c08865` |
| Consumed Vercel native provider stream | `1ecf40c74db79901cf58d138e7fad0ca` |
| Real browser inference through collector | `c8730bae44f3ea685e1e4fc7398ddd90` |
| Spawned benchmark child with preserved startup hook | `dad1f3db7907d31ffa74a220732a9fdd` |

Roots/children, observation types, available usage and model metadata were checked.
No persistent memory writes were required. Sanitized readback and UI screenshots
are under ignored `artifacts/evaluation/otel-audit-20261012/`; those local artifacts
are not a checked-in durable ledger. Local custom models can have `modelId=null`
while their configured/reported model name is present in observation metadata.

Protocol references: [Langfuse OTLP integration](https://langfuse.com/integrations/native/opentelemetry)
and [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/).

## Matching the localhost LLM baseline

The service now accepts optional startup settings for the OpenAI-compatible LLM:

```text
MEM0_LLM_TEMPERATURE=0
MEM0_LLM_TOP_P=1
MEM0_LLM_MAX_TOKENS=256
MEM0_LLM_REQUEST_TIMEOUT=30
MEM0_LLM_MAX_RETRIES=0
MEM0_LLM_EXTRA_BODY_JSON={"chat_template_kwargs":{"enable_thinking":false}}
```

These settings do not change existing defaults when absent. They apply only to
the LLM, not embedding retries/timeouts. The SDK timeout does not replace the
whole-run watchdog. Persisted `/configure` overrides can supersede startup
settings; verify the effective redacted configuration before inference. The
model/base URL and embedding dimensions must also match the selected baseline.
Do not enable this configuration on an existing personal store as an evaluation
shortcut. Tracing does not change LLM defaults or override persisted configuration.
