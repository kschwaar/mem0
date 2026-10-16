# Localhost service acceptance scorecard

Status: acceptance unproven; targets are proposed, not user-approved or achieved.
This is the historical 2026-10-07 proposed scorecard. Its current-status cells
are retained as that checkpoint, not live progress. See [the active goal](active-goal.md)
for the completed histories, reviewed failure and judge false positive.
Companion to benchmark-integration.md, not a replacement for the full goal.

| Requirement | Proposed target | Evidence required | Current status |
| --- | --- | --- | --- |
| Structured output | >=99.5% first-attempt valid | Raw runtime output, finish state and reviewed schema per attempt | Tiny diagnostics only |
| Extraction | precision >=98%, recall >=95% | Reviewed atomic facts and unsupported claims, frozen retention policy | One source case; learning-interest policy unresolved |
| Attribution | >=99% | Speaker/source-specific gold, supported versus misattributed claims | One actual HTTP user/sister trajectory passes; broad/adversarial coverage missing |
| Correction | >=99% current-state accuracy | Target IDs, before/after state and historical-fact preservation | One actual HTTP correction preserves target ID and unrelated sister record; broad coverage missing |
| Retrieval | Recall@10 >=95%, nDCG@10 >=0.90 | Source-span to memory-ID lineage and graded relevance | Two actual HTTP queries rank gold first on a two-memory synthetic scope; population labels/coverage incomplete |
| End-to-end QA | >=90% overall, >=85% each required type | Frozen independent-group workload, answerer/judge protocol and reviewed judgments | Not measured |
| Abstention | >=95% | Reviewed unanswerable cases and appropriate refusal criteria | Not measured |
| Isolation/destructive safety/persistence | zero failures in declared critical set | Actual HTTP auth, fresh storage, ID/state assertions, process restart and error recovery | Narrow SDK probes only |
| Latency | warm p95 search <=2s, add <=10s | Declared workload, store size, hardware, load state and enough observations | Single add/search timings, not p95 evidence |
| Desktop usability | no warning/critical pressure, OOM or sustained new swap growth; usable normal apps | Concurrent resource sampling and user-observed responsiveness with full service stack | Docker/Langfuse point samples available; full-stack time series and normal-app confirmation missing |
| Observability | linked question/HTTP/LLM records, verified Langfuse readback | Actual service spans, prompt/model identity, export/readback and local fallback reconciliation | HTTP ledger and tested Python span instrumentation exist; live export/readback missing |
| Repeatability | immutable input/config/source/model protocol and reconcilable outcomes | Frozen manifest, planned/completed counts, failure-visible checkpoints | Harness pieces tested; complete benchmark path unverified |

## Denominators and uncertainty

Never report only successful responses. Report planned, dispatched, completed,
errored, malformed, skipped, interrupted and semantically graded counts separately.
Structured validity includes failed first attempts in the reliability denominator.
QA acceptance reports errors/omissions as failures on the frozen planned set;
conditional accuracy among graded outputs may be supplementary only. An incomplete
or provenance-unknown run cannot pass acceptance. Semantic precision/recall use
reviewed claims/facts, not phrase presence or generated outputs promoted to gold.

Declare whether a numerical target is a point estimate or requires a lower
confidence bound before interpreting it. For an illustrative independent Bernoulli
success metric with zero failures, the one-sided exact 95% lower bound is
`0.05 ** (1/n)`. To put that bound at or above 99.5%, 99%, 98% or 95% requires
598, 299, 149 or 59 independent trials respectively. These are best-case counts,
not recommendations to dispatch those workloads now, and do not apply directly
to correlated facts, retrieval nDCG or ten conversations with many questions.
Use conversation-group uncertainty for benchmark QA; repetitions on one case do
not increase the number of independent source histories. A zero-failure critical
set demonstrates that set, not a universal absence of faults.

## Immediate acceptance blockers versus safe preparation

Docker was restored with approval; Langfuse 4.53.0 reports healthy on port 4000.
Six existing Langfuse containers are running. The fresh isolated evaluation
stack now runs Mem0, PostgreSQL and Qdrant, and GET /docs returned HTTP 200;
see artifacts/evaluation/runs/isolated-service-startup-2026-10-07.md.
Authentication, effective configuration, model access and full desktop capacity
remain unverified. User confirmed responsive normal apps before startup;
post-startup point samples do not establish full capacity acceptance.
Authenticated Langfuse export/readback remains
unproven; project credentials are absent from the coordinator environment.
No complete downloaded history fits the current initial workload bounds. Retention
policy needs a user choice before semantic gold is frozen. These conditions do
not authorize automatic budget increases, production-store writes or default
model/prompt changes. Whole-run supervision, immutable provenance and Python span
instrumentation remain safe implementation work while those gates are unresolved.

## Required actual-service safety matrix

Execute only against a separately provisioned evaluation service/store. Each case
needs planned steps, HTTP status/request IDs, before/after memory IDs and contents,
and a reconciled outcome. A skipped or interrupted case leaves its gate unproven.

| Gate | Required cases | Failure criterion |
| --- | --- | --- |
| Authentication | Missing, invalid and valid credentials on add/search/list | Unauthorized state change or protected data disclosure |
| Scope isolation | Distinct users, agents and runs; scoped search/list; unscoped requests assessed against the actual documented API contract | Data crosses an explicitly enforced boundary; do not assume entity IDs are authorization boundaries |
| Mutation integrity | Add, explicit correction, duplicate/no-op, supported deletion; unrelated user's record present throughout | Wrong target altered/deleted, unsupported fact introduced, stale current-state answer, or unrelated record changed |
| Persistence | Record IDs/content before and after actual service-process restart with the same evaluation storage | Lost/changed records, silently recreated store, or claim of restart based only on reconstructing an SDK client |
| Failure recovery | Invalid payload, model timeout/unavailability and malformed model output; inspect state before subsequent work | False success, hidden failure, corrupted state, or automatic replay of an ambiguously completed write |
| Observability failure | Export endpoint unavailable during a bounded operation; reconcile local records after recovery | Required evidence lost or instrumentation changes the service's documented result/error behavior |

Deletion and restart are scoped test cases, not permission to operate on an existing
personal service/store. Freeze the supported API behavior before writing expected
statuses; SDK/provider tests cannot satisfy these HTTP gates.

## Hardware and latency measurement contract

Measure with the accepted generation model, embedding model, isolated service and
Langfuse stack resident, plus the user's normal desktop apps. Record cold and warm
operations separately, input sizes/store population and serial concurrency. Report
sample counts and every individual duration before a p95; a tiny smoke sample
does not establish a stable tail-latency target.

Use time-series pressure, swap usage and swap I/O alongside operation timestamps;
do not treat macOS `memory_pressure`'s free-percentage summary as literal free RAM.
Existing swap usage is not itself new swap growth. The concrete sampling cadence,
growth window/threshold, minimum disk reserve and responsiveness check still need
to be frozen before a live capacity experiment. Until then desktop acceptance is
unknown, even if a before/after snapshot looks favorable. No automatic increase in
workload or concurrency follows from an offline test passing.

## Python runtime tracing audit (2026-10-07)

The service's current `server/main.py:log_requests` sets an eight-character
request ID in a ContextVar and returns it in `X-Request-ID`; `server/errors.py`
uses the same context for redacted upstream errors and logging. This is request
correlation, not an LLM span or Langfuse export. No Langfuse/OpenTelemetry hooks
were found in the Python server sources, and `server/requirements.txt` declares
neither tracing SDK/exporter. The existing exporter lives in
`mem0-ts/scripts/evaluation_tracing.ts`, not this Python execution path.

`scripts/evaluate_memory_trajectory.py:Trace` wraps selected provider methods
after constructing an SDK Memory instance. It writes local adapter timings, but
has no model-response usage/finish information, physical-attempt accounting or
Langfuse export. Its mutable stage field is suited only to that sequential
diagnostic, not concurrent HTTP runtime tracing. It is not installed on the
server's singleton. Do not promote those traces to runtime tracing acceptance.

The server constructs Memory in both `server/server_state.py:initialize_state`
and `update_config`. Instrumentation must cover both constructions exactly once;
adding it only at initial startup would silently lose tracing after a config
update. Capture extraction and mutation/procedural calls without relying on
prompts to identify them. OpenAI SDK boundary instrumentation is additionally
needed for raw finish state, usage and response-model identity; adapter timing
alone cannot provide these fields. Distinguish observed SDK invocations from
physical transport attempts rather than inferring retry counts.

Implementation gates: reuse the project's OpenTelemetry-to-Langfuse approach
with optional Python dependencies, request-context isolation tests, configured
instance lifecycle tests, metadata-only default/privacy bounds, original-result
preservation during exporter failure, and an actual Docker add/search readback.
Keep local reconciliation independent of exporter availability. Benchmark
question/input hashes must link to the service request ID without trusting
caller-supplied IDs as authorization. Initialization calls without request
context must be labeled separately, not assigned to a later benchmark question.
An initial provider-span helper now exists in `server/runtime_tracing.py`.
Twenty-two model-free tests cover single attachment, request-ID lookup per call,
metadata privacy, original return/exception preservation and tracing/logging
failure isolation. It accepts a supplied OpenTelemetry tracer but is not yet
enabled by default. The explicit
`server_state.set_runtime_tracer` registration hook now instruments both Memory
construction paths; tests cover configuration replacement and overlapping
ContextVar-scoped calls. Main.py now configures/registers an explicitly enabled
exporter, activates HTTP roots and flushes/shuts down via lifespan. The middleware
test executes its actual AST in a minimal app; full startup/deployment is untested. No runtime
trace acceptance or live readback follows from these helper tests.
The optional bounded OTLP exporter factory and local endpoint/credential
validation now exist. Real SDK construction and in-memory request/child span
parentage are tested with isolated permanent tracing dependencies; network export
has not been tested. See `server/RUNTIME_TRACING.md` for exact
configuration and remaining limits. Optional packages are installed only under
permanent evaluation artifacts, not in the service/repository environment.
The actual OpenAI adapter with mocked SDK IO also verifies nested SDK generation
spans with response model, first-choice finish reason and token metadata. This
does not count physical requests or prove oMLX/Langfuse behavior.
