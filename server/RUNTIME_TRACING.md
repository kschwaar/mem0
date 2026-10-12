# Optional Python runtime tracing

Implementation is now wired into startup, HTTP middleware and FastAPI shutdown,
but remains **disabled by default**. Actual Docker export/readback is unverified;
do not treat configuration or a successful flush as delivered traces.

`runtime_tracing.build_exporter(env)` returns a tracer and an owned provider only
when `MEM0_RUNTIME_TRACING=true`. Otherwise it returns None without importing
optional tracing packages. Enabled tracing needs the separately declared
`requirements-tracing.txt` in the chosen Docker image; no installation has been
performed in the service environment. Optional packages were downloaded into
permanent `artifacts/evaluation/environments/tracing-deps` for isolated tests;
use that directory on PYTHONPATH only for the declared evaluation command.

Explicit settings, stored outside git:

```text
MEM0_RUNTIME_TRACING=true
MEM0_RUNTIME_TRACE_ENDPOINT=http://host.docker.internal:4000/api/public/otel/v1/traces
LANGFUSE_PUBLIC_KEY=<project public key>
LANGFUSE_SECRET_KEY=<project secret key>
```

The example assumes host Langfuse on port 4000; verify the actual endpoint first.
Only explicitly allowed local/Docker hostnames are accepted. URLs cannot contain
credentials, query strings or fragments. Headers use in-memory Basic auth and
Langfuse's OTLP ingestion-version header. Never serialize or log the returned
headers/settings. Local names do not prove DNS resolution or exporter network
isolation; deployment/firewall verification remains necessary.

The batch queue is bounded to 256 spans, batches to 32, and exporter timeout to
five seconds. These bounds do not make export durable or guarantee every span
is delivered. The application must own provider flush/shutdown and verify export
failures independently; the HTTP ledger remains separate evidence.

Register the supplied tracer using `server_state.set_runtime_tracer(tracer,
request_id_getter)` before Memory initialization. Both initialization and config
replacement attach the wrapper. The helper records method/role, request ID,
elapsed time and returned/raised state—not prompts, outputs, vectors or raw
exception text. Adapter invocation counts are not physical transport attempts.

For compatible clients exposing `client.chat.completions.create`, a nested SDK
generation span records reported response-model identity, first-choice finish
reason and available token counts (including cached/reasoning subcounts).
Subcounts overlap their parent input/output counts; do not add them again to
total usage. Missing usage is unknown, not zero. Other providers, streaming and
physical retries are not covered by this SDK hook. No raw response is exported.

HTTP middleware creates a root context with the server-generated request ID.
Raw URLs are excluded; the route field is currently `unresolved`. Shutdown
flushes the queue off the event loop and attempts provider shutdown. Abrupt
process termination can still lose spans. Tests exercise the actual middleware
function extracted into a minimal app, not the full server import/startup path.

Still required: other-provider SDK coverage and server-phase/physical-attempt metadata,
durable fallback, privacy-approved focused dataset capture, actual container
configuration and linked Langfuse readback. These helpers alone do not satisfy
the service quality or observability acceptance gates.

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
shortcut. No deployed configuration was changed by the implementation.
