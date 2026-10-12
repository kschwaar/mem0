# Official benchmark integration with this fork

## Current scope — supersedes the historical sequencing below

As of 2026-10-12, two complete LongMemEval histories have run through the isolated
HTTP service. One case passed reviewed checks; the other failed retrieval and
exposed a false-positive answer judge. See [the active goal](active-goal.md) for
current evidence, diagnosis and limits. Status claims dated 2026-10-07 below are
historical, including statements that no wrapper or full history had run.

Follow [the active goal](active-goal.md). Reuse memory-benchmarks against the
existing isolated actual-product service, with Gemma and Qwen3-Embedding served
by oMLX. Complete the existing authenticated adapter's prediction/scoring and
checkpoint workflow; do not build a second benchmark wrapper. Start with one
complete LongMemEval history, then a small fixed category slice. Langfuse is
diagnostic enrichment, not a prerequisite for the initial benchmark result.

The remaining sections preserve earlier analysis and commands, including stale
startup status and superseded prerequisites. They are not the current execution
sequence. Do not interpret them as authorization for live runs or production changes.

Status: integration remains incomplete. A direct-SDK tiny trajectory has run;
cleaned LongMemEval S is now downloaded and hash-verified in permanent local
`artifacts/evaluation/` storage (excluded from git). No Docker wrapper or full
benchmark history has run. Commands below are opt-in; this file does not automate them.

The 2026-10-07 inventory found no complete cleaned-S history within the initial
100-chunk / 250,000-character envelope. Minimum observed positional two-turn
chunk count is 199; minimum content character count is 454,980. No full question
is selected. Counts are work estimates, not verified provider-call or token counts.
The pinned dataset card declares MIT. See local `artifacts/evaluation/dataset-inventory.md`
for measured coverage and limitations. Prepare a separately labeled short diagnostic
before reconsidering bounds; do not call a truncated history a standard benchmark run.

### Current evidence and unresolved acceptance gates (2026-10-07)

- LongMemEval exact session-ID/content grouping connects all 500 questions into
  one component. A strict history-disjoint tuning/acceptance split within that
  file is impossible under the selected protocol. It remains development and
  descriptive benchmark material, not an independent holdout.
- LOCOMO is downloaded at `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`
  under permanent `artifacts/evaluation/datasets/locomo/`. Its license is CC BY-NC
  4.0. Ten conversations are a small independent-group base; all exceed the
  initial ingestion-count limit. Speaker-aware adaptation and eligibility remain
  open. Do not send generated summaries/observations or QA annotations to ingestion.
- Exact cross-source turn-text auditing found one match: LOCOMO `conv-30`,
  `D13:19`, text `Thanks!`. This is a generic acknowledgment, not evidence of
  shared substantive source histories. It does not prove zero near-duplicate or
  semantic leakage. Keep the match in the audit; do not silently exclude it to
  claim perfect independence.
- The one-add source-derived Gemma SDK diagnostic completed six operations,
  seven phrase checks, and unchanged memory-ID reload/retrieval. It captured
  enjoyment of Premiere Pro, not the explicit advanced-settings learning interest.
  Required retention of that second fact is policy-pending. No production-quality
  inference follows from green phrase checks.
- Docker was restored with approval after the earlier absent-socket probes.
  Six Langfuse containers are running and health reports version 4.53.0; no
  Mem0 container is running. Coordinator project credentials are absent, so
  authenticated export/readback remains unproven. Re-measure normal-desktop
  capacity before provisioning the isolated evaluation service; do not reuse
  Docker-stopped SDK readiness as service-stack acceptance.

Local inventory, overlap manifests, diagnostic outputs and reviews live under
`artifacts/evaluation/` (git-ignored). The next work should prioritize the
speaker/role contract, Python tracing and isolated actual-service adapter over
additional loosely scoped dataset research. Retention-policy approval is needed
before semantic acceptance labels are frozen; it does not block model-free
adapter and observability work.

The product-local HTTP adapter now has serial async dispatch, an exclusive local
request ledger and fail-closed ingestion reconciliation. The permanent adapted
LongMemEval runner supports `--question-id` and `--ingest-only`; the source-launch
supervisor verifies dataset/overlay hashes before dispatch and fixes one worker.
Model-free tests cover these pieces, including a real synthetic child process.
The complete prepared runner/source supervisor has now also been exercised against
a loopback mock HTTP server: valid ingestion reconciles; malformed envelopes and
HTTP 500 remain failures without retries. This verifies orchestration with mock
responses, not actual-service execution or semantic quality.
Keep the pristine pinned suite separate from the adapted runner, and do not use
the launcher on an existing personal store. See `acceptance-scorecard.md` for the
actual-service safety matrix and remaining acceptance requirements.

A separate actual-service Compose preparation now lives at permanent
`artifacts/evaluation/service-stack-v1/`. Fixture-only config validation passes;
it has project-scoped volumes, authenticated API on loopback 8898 and no existing
store mounts. It has not been built or launched. Image/source/dependency pins,
offline assets and hardware readiness are prerequisites, not inferred from a
valid Compose file. See its README before any startup.

## Integration choices and recommendation

Reviewed against the pinned benchmark sources and this fork on 2026-10-06.
This document is the central implementation plan. The adjacent research files
provide background; they are not additional competing work plans.

The runtime is **oMLX**, not Ollama: native URL `http://127.0.0.1:8080/v1`.
The original 27B model did not meet comfortable desktop-memory requirements.
Gemma 4 12B is now the first experimental stateful candidate, with Qwen3 14B as
a comparison baseline; neither is production-accepted. Use the
[oMLX readiness report](omlx-readiness.md), [Gemma config](gemma-omlx-python.json)
and [first SDK trajectory evidence](gemma-trajectory-first.md). The Docker
wrapper has not run; historical Ollama examples are alternatives only. Recheck
resources with normal workloads before each subsequent live stage.

| Option                                                 | What it measures                                                 | Required integration                                                                                              | Use                                                                                              |
| ------------------------------------------------------ | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| A: isolated benchmark wrapper in Docker                | Exact fork Python SDK, local models and fresh Qdrant             | Adapt pinned wrapper, build immutable fork image, instrument model calls                                          | Recommended first executable baseline; detailed recipe below                                     |
| B: isolated copy of the actual localhost service stack | Product HTTP/auth/config/storage behavior plus SDK/model quality | Adapt benchmark client to product API and authentication, provision a separate evaluation stack                   | Required final service acceptance; do not point ingestion at an existing personal store          |
| C: direct SDK benchmark backend                        | SDK/model behavior without HTTP/container overhead               | Implement the benchmark client's async interface against local Memory, including cleanup and result normalization | Useful diagnostic comparison; does not establish HTTP-service quality                            |
| D: TypeScript benchmark bridge                         | TypeScript OSS contracts and models                              | Separate Node HTTP bridge or runner backend with its own contract tests                                           | Add only if TypeScript is a deployed runtime; Python results do not establish TypeScript quality |

Start with A to reuse the suite with the smallest compatibility surface, then
run B using the accepted runtime configuration. C helps isolate HTTP/storage
overhead if needed. Follow `server/AGENTS.md`: the actual service stack runs
through Docker Compose, not a new native uvicorn deployment path.

The three endpoint roles are independent: benchmark runner to Mem0; Mem0 to
extraction/embedding/reranking models; runner to answerer/judge models. Docker
containers reach host models through `host.docker.internal`; a native runner
uses `127.0.0.1`. Validate each route separately, including model availability,
embedding dimensions and effective sampling configuration. An extraction-only
local config is insufficient to establish an entirely local experiment.

## Implementation work packages and completion gates

### Resource-aware ramp: no automatic full-suite launch

Concurrency stays at one through the initial quality runs. Only one inference
experiment runs at a time; answerer and judge passes run after ingestion and
retrieval, rather than competing for model memory. Do not pull a new model as a
side effect of a readiness check. Review installed and resident model metadata
and available memory/disk before choosing a configuration.

| Level | Workload                                                   | Initial bound and advance condition                                                                                            |
| ----- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 0     | Pure compatibility and evaluator tests                     | No models, dataset download or service startup; contract checks must pass                                                      |
| 1     | Hardware, endpoint and installed-model metadata            | Read-only GET probes with short timeouts; record RAM, swap, Docker ceilings, resident models and disk                          |
| 2     | One embedding and one short extraction                     | Serial calls, one repetition, no judge/reranker; inspect dimensions, contract validity, latency and resource deltas            |
| 3     | Tiny authored trajectory                                   | At most three adds and nine total operations; fresh storage; correction/isolation/reload; five-minute initial wall-time budget |
| 4     | One complete benchmark question history                    | Inventory first and choose an eligible low-cost development history; concurrency one, top-k 10, no answerer/judge initially    |
| 5     | Three complete development histories                       | Only after level 4 completes within measured time and memory bounds; run sequentially with distinct question IDs               |
| 6     | One complete history per required category plus abstention | Freeze IDs; estimate total ingestion from prior rates; separate answerer/judge pass after resource review                      |
| 7     | Broader held-out acceptance and longer histories           | Budgeted batches with checkpoints; full suite and BEAM are later milestones, not default next commands                         |

Before level 4, use a conservative initial pilot envelope of 100 ingestion
pairs, 250,000 input characters and 15 minutes, including the full selected
history. These are provisional work bounds, not tokenizer counts or model-call
counts: each add may invoke several providers and include prior memory context.
If no complete history fits, emit no selected question and a budget-exceeded
report. Adjust the envelope after the tiny trajectory's measurements; never
silently truncate a history and call its score a standard benchmark result.
A shortened history may be a separately labeled diagnostic fixture.

Record a resource baseline before each stage. Pause dispatch at macOS warning
or critical memory pressure, sustained new swap growth, model/container OOM,
repeated timeouts, or significant degradation of normal desktop responsiveness.
Treat missing resource measurements as unknown. Resolve concrete free-memory,
swap-growth and disk thresholds from this machine's inventory before inference.
A wall-time cap must stop dispatch and persist incomplete work; it is not proof
that an already-running provider request was cancelled. Use serving request
timeouts as well. Do not mark incomplete calls as passed or completed.

Keep model context/output limits and resident-model memory recorded. Load cold
models serially, distinguish cold/warm results, and avoid judging with a larger
model until its memory requirements are established. Increase dataset coverage
before concurrency. A concurrency-two capacity probe is a separate later
experiment after quality and memory bounds pass, never the default ramp.

1. **Compatibility adapter.** Pin the suite and fork, implement request and
   response translation, and test add/search/list, isolation, supported custom
   prompts, unsupported fields and error handling without models. Reject
   malformed responses instead of turning them into empty memories. Gate:
   contract tests pass and the image reports the intended installed source.
2. **Dataset preparation.** Download revision-pinned cleaned LongMemEval into
   an external artifact directory. Inventory actual IDs/types, sessions, input
   characters/tokens where measurable, and ingestion pairs. Create a selection
   and conversation-group split manifest before inference. Gate: hashes,
   license, selected IDs, leakage checks and estimated work are recorded.
3. **Local readiness and trace capture.** Resolve installed model IDs/digests,
   endpoint roles and storage isolation. Add Python wrapper/runtime tracing;
   the existing TypeScript evaluator instrumentation does not automatically
   instrument this Python benchmark service. Gate: one authored add/search
   produces linked HTTP, adapter and observed SDK spans with readback in
   Langfuse, and results survive observability failures in the local ledger.
4. **Small baseline.** Start with one inventoried complete low-cost development
   history, then three, then one per required type only after the resource gates
   above pass. Keep selected IDs frozen. Ingestion/retrieval prediction is separate from
   answering/judging. Gate: planned versus completed calls reconcile; no
   failed ingestion is treated as a completed successful checkpoint; failures
   and skips remain visible. This slice is a smoke check, not acceptance.
5. **Focused datasets.** Export captured calls and prior memory snapshots into
   stage datasets, review labels, and run the existing AI-call harness where
   its contracts apply. Gate: trace lineage and label provenance are retained;
   held-out conversations never enter prompt tuning.
6. **Quality comparison and service acceptance.** Freeze answerer/judge and
   retrieval settings while changing one subject component. Expand the held-out
   sample, then full LongMemEval, suitable LOCOMO and bounded BEAM histories.
   Repeat accepted configurations through option B, including reload, error
   recovery, isolation and load. Gate: all required quality targets pass on the
   declared workload, with per-category results and uncertainty.

Each package produces one reviewable artifact: adapter/tests, data manifest,
trace smoke report, baseline report, reviewed stage datasets, or acceptance
report. No package is complete merely because its command exits zero.

## Bootstrap data before production data exists

Yes: benchmark datasets can be the first realistic workload. The suite
downloads data from the dataset authors rather than providing a production
corpus from this machine. Start with cleaned LongMemEval; preserve its question
IDs, session IDs/dates, ground-truth answers and evidence-session annotations.
Use the full selected histories for the end-to-end baseline: removing distractor
sessions changes the task and must be a separately named diagnostic experiment.

Split source conversations before running tuning experiments or mining traces.
Group shared/overlapping histories together using source IDs and content
fingerprints; question-ID-only splitting can leak the same material. Retain
`has_answer` and evidence annotations in the labeling sidecar only; exclude them
from model inputs. Hold acceptance groups out of prompt/model selection. The
small benchmark corpus supplies initial coverage, not proof of fit for all
future personal use cases.

| Targeted dataset                    | Reusable benchmark material                                     | Additional labeling required                                                                                      |
| ----------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Extraction and attribution          | Ingestion message pairs and their actual extraction prompts     | Reviewed atomic facts, attribution, negatives and unsupported claims; QA answers are not complete extraction gold |
| Update/delete/no-op                 | Chronological pairs plus captured pre-call memory state         | Correct action and target ID for this SDK's contract; benchmark question types alone do not label mutations       |
| Embedding/retrieval                 | Questions, evidence sessions, saved memories and source lineage | Map derived memory IDs to supporting source spans; evaluate evidence sufficiency separately from exact ID recall  |
| Reranking                           | Fixed candidate pools before reranking, query and evidence      | Reviewed graded relevance; hold candidate pools fixed across reranker comparisons                                 |
| Temporal/abstention                 | Dated histories, update questions and unanswerable items        | Explicit date protocol, expected current state and evidence; date-in-text is not wall-clock aging                 |
| Relationships/procedural/multimodal | Applicable text excerpts, when present                          | Author dedicated gold labels and fixtures; suite QA labels do not establish these stage contracts                 |

Derived case records should contain dataset revision/hash, source question,
session and turn IDs, split, original run/trace/observation IDs, stage/prompt
hash, input and pre-state snapshot, reviewed expectations, reviewer and label
version. Capture embedding/retrieval input text and candidate IDs rather than
exporting large vectors into Langfuse. Model-generated outputs are candidates
for review, never gold merely because they were produced during a benchmark.

Organize Langfuse datasets under `mem0/extraction`, `mem0/mutations`,
`mem0/retrieval`, `mem0/reranking` and `mem0/temporal`; retain the same cases in
versioned local JSONL so experiments remain reproducible without Langfuse.
Link each stage score to its observation and each end-to-end score to its
question root. The existing evaluator exports contract grades; semantic
precision/recall and relevance labels require additional graders. Tune with
the stage harness, then confirm improvements on untouched end-to-end cases.

## Quality decision protocol

The initial proposed goals are extraction precision >=98% and recall >=95%,
first-attempt structured validity >=99.5%, attribution >=99%, correction-state
accuracy >=99%, evidence Recall@10 >=95% and reviewed nDCG@10 >=0.90. End-to-end
QA targets are >=90% overall, >=85% per required category and >=95% abstention
accuracy. Isolation, unintended deletion and persistence losses have zero
tolerance in the critical acceptance set. These are design targets to validate,
not official Mem0 benchmark promises or already achieved results.

Measure appropriate metrics only when labels support them: the suite's
retrieval-mode judge measures sufficiency, not annotated Recall@10 or nDCG.
Report suite scores alongside custom stage metrics, with separate denominators
for quality, errors, skips and coverage. For stochastic trials, report paired
differences and uncertainty grouped by source conversation; repeated samples
of one case are not independent evidence of population quality.

Declare input lengths, history/store sizes, concurrency and hardware before
latency gates. Provisional warm p95 targets are search <=2 seconds and add <=10
seconds; cold model loads and evaluation-model time are reported separately.
Record observed retries and unknown usage explicitly. Acceptance must include
the actual product service path, not just its benchmark replacement wrapper.

Use the official suite for chronological memory QA and the adjacent focused harness for individual call correctness/reliability/load. The existing `evaluation` submodule pins `4b61c5d31b9c668a12b4f5e78064248a02c82d2b`. Keep that gitlink unchanged. An independent temporary checkout is sufficient for experiments.

## Why stock Docker is insufficient

The pinned [server requirements](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker/mem0/requirements.txt) install upstream `feat/v3-pipeline`. They do not evaluate this fork. Build a replacement server image from an archived, committed fork revision and the pinned benchmark wrapper. Omit the stock Dockerfile's SageMaker validator patch unless SageMaker is the explicit experiment; it changes the tested implementation.

The pinned [client](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/common/mem0_client.py) sends `timestamp`; the pinned [wrapper](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker/mem0/main.py) has an unused `observation_date` field but no `timestamp` field. This fork rejects non-null `Memory.add(timestamp=...)`, and the product server's `MemoryCreate` currently has no timestamp field. Therefore neither ignored fields nor chronological ingestion establish historical storage timestamps. Select an explicit protocol: preserve source dates in input text and provenance while omitting the unsupported SDK argument, or separately implement a supported temporal API and validate it. Record date-text runs as adapted benchmark runs; do not claim native temporal parity or real wall-clock aging.

**Compatibility gate:** adapt top-level entity IDs to `filters={...}` and `limit` to `top_k` for SDK search/list calls. This fork's `Memory.add` accepts `prompt`; preserve and test the mapping from benchmark `custom_instructions` rather than removing it without cause. For option B, send supported product request fields (`filters`, `top_k`, `prompt`) and configured authentication; the stock OSS client only sends authentication in Cloud mode. Product search currently does not expose every SDK/benchmark option, including rerank: reject unsupported requested behavior or add a separately tested service change, rather than silently ignoring it. Add wrapper/client translation tests and retain patch hashes. The build commands below prepare an image; they do not supply or validate these adapters. Until this gate passes, use `evaluate_memory_trajectory.py` for the direct-SDK smoke baseline.

The first pure translation helpers now exist in
[`../benchmark_compat.py`](../benchmark_compat.py), with model-free regressions
in `tests/test_benchmark_compat.py`. They translate add/search/list and validate
result envelopes. They are not yet wired into an HTTP wrapper; checkpoint
hardening, model tracing and actual-service authentication remain open work.

The product-path request planner (`../benchmark_service_requests.py`) now maps
add/search/list to `/memories` and `/search`, preserving supported fields and
rejecting explicit rerank behavior not exposed by SearchRequest. Scoped listing
uses entity-ID query parameters, not a silently ignored `filters` parameter.
The local transport (`../benchmark_service_client.py`) uses an in-memory
X-API-Key, numeric loopback origins, disabled proxies, refused redirects, bounded
response size and no automatic retries. A unique evaluation prefix is mandatory,
but this is not storage isolation: provision and verify an independent service
store before connecting it. These helpers are not yet wired into the suite's
async backend/checkpoints or trajectory runner.

Eight transport checks pass, including a real loopback mock-server test of GET
encoding, authentication and refusing a redirect without forwarding credentials.
This proves the client transport boundary only, not actual service authentication,
model behavior or Langfuse delivery. The mock uses synthetic credentials and no
Mem0/oMLX endpoint. Its socket test requires loopback permission in sandboxed runs.

The permanent suite checkout is pinned at
`artifacts/evaluation/memory-benchmarks/`. A separate checkpoint overlay lives
at `artifacts/evaluation/adaptations/longmemeval-checkpoint-v1/`; the pristine
checkout and gitlink remain unchanged. Seven local fault-injection tests cover
failed/malformed/timeout adds and valid empty/nonempty success at the actual
per-chunk statement boundary. The overlay stops before recording a failed chunk
as complete. Full-history completion, durable resume and outer runner exception
handling are still unverified. Do not trust or resume stock checkpoints produced
before this hardening; ambiguous timed-out writes need reconciliation, not retries.

`../benchmark_async_backend.py` now exposes serial async add/search methods over
the product transport. Four model-free tests cover request translation, visible
errors, serial dispatch and repeated coroutine cancellation retaining the serial
slot until the underlying transport finishes. This is not server cancellation;
transport inactivity timeouts are not whole-run budgets. The suite's backend
constructor has not yet been switched to it. The default search protocol is
explicitly service-default reranking; callers requesting True or False are
rejected until that behavior is supported. Destructive cleanup is rejected.
The suite's generated user IDs need an immutable evaluation-prefix mapping and
checkpoint provenance before use; never bypass prefix validation to make a run work.

Python observability inspection confirms the product middleware emits X-Request-ID
and request logs, but no Langfuse/OTLP spans were found in the inspected Python
service/SDK source. Existing TypeScript instrumentation does not cover this path.
The HTTP client now retains sanitized hex request IDs, status, one-attempt metadata
and client latency in `last_evidence`, including HTTP failures. This is in-memory
HTTP-boundary evidence only; it is not durable span capture, prompt/usage visibility
or verified Langfuse export. Bind it to question/chunk lineage in a local ledger
before adding actual server-side span propagation and readback validation.

## Prepare an immutable source snapshot

Run from the fork repository root. The clean tracked tree guard intentionally rejects unfinished work; commit the intended implementation before benchmarking it. Untracked research files do not enter `git archive`.

```bash
git status --short --branch
test -z "$(git status --porcelain --untracked-files=no)"
export EVAL_FORK_ROOT="$PWD"
export EVAL_FORK_SHA="$(git rev-parse HEAD)"
export EVAL_BENCH_SHA=4b61c5d31b9c668a12b4f5e78064248a02c82d2b
export EVAL_WORK="$(mktemp -d /tmp/mem0-benchmark.XXXXXX)"
git clone --no-checkout https://github.com/mem0ai/memory-benchmarks.git "$EVAL_WORK/benchmarks"
git -C "$EVAL_WORK/benchmarks" checkout --detach "$EVAL_BENCH_SHA"
mkdir -p "$EVAL_WORK/build/fork" "$EVAL_WORK/build/server"
git archive --format=tar --output="$EVAL_WORK/fork.tar" "$EVAL_FORK_SHA"
tar -xf "$EVAL_WORK/fork.tar" -C "$EVAL_WORK/build/fork"
cp -R "$EVAL_WORK/benchmarks/docker/mem0/." "$EVAL_WORK/build/server/"
printf '%s\n' "$EVAL_FORK_SHA" > "$EVAL_WORK/build/fork-sha.txt"
printf '%s\n' "$EVAL_BENCH_SHA" > "$EVAL_WORK/build/benchmark-sha.txt"
```

Do not run `git submodule update` for this recipe. Keep the temporary checkout and generated adaptations with run artifacts; they are part of the experiment.

## Replace the SDK source, then build

Prepare a benchmark-server dependency list excluding the upstream SDK reference. This uses the current fork's package dependencies, plus wrapper requirements and local Ollama support. No implementation source is patched.

```bash
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['EVAL_WORK']) / 'build'
source = root / 'server' / 'requirements.txt'
lines = source.read_text().splitlines()
references = [line for line in lines if line.startswith('mem0ai @')]
assert len(references) == 1, 'Unexpected pinned server requirements; review manually'
(root / 'server-requirements.txt').write_text(
    '\n'.join(line for line in lines if not line.startswith('mem0ai @')) + '\nollama\n'
)
PY
cat > "$EVAL_WORK/build/Dockerfile" <<'DOCKER'
FROM python:3.12-slim
WORKDIR /app
COPY server-requirements.txt /tmp/server-requirements.txt
COPY fork /opt/mem0-fork
RUN pip install --no-cache-dir /opt/mem0-fork -r /tmp/server-requirements.txt
COPY server /app
COPY fork-sha.txt benchmark-sha.txt /app/
RUN mkdir -p /app/history
ENV MEM0_TELEMETRY=false
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
DOCKER
docker build -t "mem0-eval-fork:$EVAL_FORK_SHA" "$EVAL_WORK/build"
docker run --rm "mem0-eval-fork:$EVAL_FORK_SHA" python -c \
  'import mem0, importlib.metadata as m; print(mem0.__file__); print(m.version("mem0ai"))'
```

This is source reproducibility, not yet dependency reproducibility. The upstream requirements use version ranges and the base image is a mutable tag. After the first successful build, record `docker image inspect`, `pip freeze --all`, Python version, and all resolved image IDs/digests. For comparable later builds, replace the base image with its digest and dependency ranges with a reviewed lock (including transitive packages and hashes). Archive the built image or retain an immutable registry digest. The stock spaCy setup is omitted: the archived fork's vector pipeline must be verified against the wrapper before a full run. If its selected configuration requires spaCy or graph dependencies, add and pin only the applicable packages.

## Local model configuration and isolated storage

Choose an exact installed Qwen tag and separately choose an embedding model. Determine embedding dimensions from that model, not from the LLM. Pin Ollama version, model digest, quantization, context size, hardware and sampling parameters. The following example uses `nomic-embed-text` dimensions of 768; verify the endpoint response before retaining that value. Replace the Qwen tag with the intended installed model. No model pull is performed here.

Create `$EVAL_WORK/mem0-config.yaml`:

```yaml
version: v1.1
llm:
  provider: ollama
  config:
    model: REPLACE_WITH_EXACT_INSTALLED_QWEN_TAG
    ollama_base_url: http://host.docker.internal:11434
    temperature: 0
embedder:
  provider: ollama
  config:
    model: nomic-embed-text
    ollama_base_url: http://host.docker.internal:11434
    embedding_dims: 768
vector_store:
  provider: qdrant
  config:
    host: qdrant
    port: 6333
    collection_name: evaluation_only
    embedding_model_dims: 768
history_db_path: /app/history/history.db
```

This host routing matches Docker Desktop on this Mac; other systems require an explicitly verified route. Provider configuration follows the official [Ollama example](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/configs/ollama.yaml).

Create `$EVAL_WORK/compose.yaml`. Set `EVAL_QDRANT_IMAGE` to a reviewed Qdrant image digest before running; do not use the official compose's `latest` for comparison runs.

```yaml
services:
  qdrant:
    image: ${EVAL_QDRANT_IMAGE:?Set an immutable Qdrant image digest}
    volumes:
      - qdrant_data:/qdrant/storage
  mem0:
    image: mem0-eval-fork:${EVAL_FORK_SHA:?Set fork commit}
    ports:
      - "127.0.0.1:8888:8000"
    environment:
      MEM0_CONFIG_PATH: /app/config.yaml
      MEM0_TELEMETRY: "false"
    volumes:
      - ./mem0-config.yaml:/app/config.yaml:ro
      - history:/app/history
volumes:
  qdrant_data:
  history:
```

```bash
export EVAL_PROJECT="mem0eval-$(date -u +%Y%m%d%H%M%S)"
# Set EVAL_QDRANT_IMAGE to qdrant/qdrant@sha256:... before proceeding.
docker compose -p "$EVAL_PROJECT" -f "$EVAL_WORK/compose.yaml" up -d qdrant
# Confirm Qdrant readiness in container logs before starting the server.
docker compose -p "$EVAL_PROJECT" -f "$EVAL_WORK/compose.yaml" logs qdrant
docker compose -p "$EVAL_PROJECT" -f "$EVAL_WORK/compose.yaml" up -d mem0
curl --fail http://127.0.0.1:8888/health
```

Use a unique compose project, fresh volumes and run name for every model/config comparison. Reuse checkpoints only when all config, source and dataset hashes match. Stop with `docker compose -p "$EVAL_PROJECT" -f "$EVAL_WORK/compose.yaml" down`; this preserves artifacts and volumes. No destructive volume cleanup is part of this recipe.

## Runner setup and bounded first run

Use an isolated virtual environment; no root or repo dependency changes:

```bash
python3 -m venv "$EVAL_WORK/runner-env"
"$EVAL_WORK/runner-env/bin/python" -m pip install -r "$EVAL_WORK/benchmarks/requirements.txt"
cd "$EVAL_WORK/benchmarks"
"$EVAL_WORK/runner-env/bin/python" -m benchmarks.longmemeval.run --help
"$EVAL_WORK/runner-env/bin/python" -m pip freeze --all > "$EVAL_WORK/runner-freeze.txt"
```

Review available flags from the pinned runner. Supply a predownloaded, revision-pinned cleaned LongMemEval file via `--dataset-path` and record `shasum -a 256` plus selected question IDs. Avoid a live `resolve/main` dataset for stable comparisons.

The runner instantiates answerer and judge even in prediction mode. Set them to a configured local OpenAI-compatible endpoint with a non-secret placeholder key, and ensure no `.env` in the temporary benchmark checkout overrides those values (its dotenv loading uses `override=True`). These settings apply to evaluation models, separately from extraction and embeddings:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1
export OPENAI_API_KEY=local-placeholder
# Replace both placeholders with the exact installed local evaluation model tag.
"$EVAL_WORK/runner-env/bin/python" -m benchmarks.longmemeval.run \
  --project-name fork-qwen-smoke-unique \
  --backend oss --mem0-host http://127.0.0.1:8888 \
  --dataset-path /absolute/path/to/pinned-longmemeval-s-cleaned.json \
  --per-type 1 --seed 42 --max-workers 1 --top-k 20 --top-k-cutoffs 20 \
  --answerer-model EXACT_LOCAL_ANSWERER --judge-model EXACT_LOCAL_JUDGE \
  --predict-only
```

At this pinned revision, `--predict-only` performs ingestion and retrieval and
returns before answer generation and judging. It is a retrieval capture pass,
not an answer-quality score. It still makes extraction and embedding calls for
all selected histories; one question per type can contain many ingestion calls.
Estimate message-pair counts beforehand and set an external wall-time/resource
budget. Verify local routing before launching. A separate `--evaluate-only` pass
in answerer mode generates answers and judges saved retrievals; retrieval mode
judges evidence sufficiency. Review the pinned runner's help and saved-result
path before selecting either mode. Do not change the subject model during a
checkpoint resume. Keep an independent, fixed judge for comparisons and manually
review representative judgments. Separate judge failures from quality failures.

The pinned ingestion runner checkpoints failed pairs and marks a question
complete with a failure count. Harden the experiment driver before resumable
acceptance runs: failed pairs must remain failed/incomplete, completed ingestion
must require zero failures, and a resume must reconcile source/config/data hashes
and persisted storage identity. After a transport-ambiguous add, determine
whether the mutation happened before retrying; retries can duplicate writes.
Preserve completed evidence and retry only validated outstanding work.

## Longitudinal experiment plan

1. Validate server API compatibility on a tiny authored conversation: ingestion, retrieval, updates, no-op decisions, isolation and malformed model responses. Verify it calls the archived fork and records each AI attempt.
2. Compare a fixed six-question LongMemEval slice with one extraction model changed at a time. Keep embedding, judge, answerer, retrieval cutoff, hardware and data constant. Repeat each configuration to expose variance.
3. Run all 500 cleaned LongMemEval questions after budgeting, then selected LOCOMO conversations and one BEAM 100K conversation. Increase BEAM size only after measured time/cost estimates.
4. Add fork-specific chronological tests: facts corrected repeatedly, stale preferences, unrelated distractors, contradictory entities, delayed queries, expiration/decay and graph toggles. Track memory state after each event and retrieve at controlled horizons. Run real wall-clock and simulated-time protocols separately.
5. Report first-attempt and eventual success, contract correctness, evidence recall, memory precision, mutation correctness, end-to-end QA, abstention, latency percentiles, throughput, retries, tokens and resource usage. Preserve failed requests rather than allowing them to disappear into empty results.

Record source SHAs, wrapper patch hashes, full redacted config, data revision/hash, case IDs, prompts, outputs, trace IDs, judge version, model digests, serving engine, image digests, dependency locks and cold/warm state. Publish paired quality differences with uncertainty; small slices are smoke checks, not reliable population estimates.

## Licensing and source references

Benchmark code is [Apache-2.0](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/LICENSE). Dataset rights are separate: [LOCOMO](https://github.com/snap-research/locomo/blob/main/LICENSE.txt) is CC BY-NC 4.0, [cleaned LongMemEval](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) labels MIT, and [BEAM](https://huggingface.co/datasets/Mohammadta/BEAM) plus [BEAM-10M](https://huggingface.co/datasets/Mohammadta/BEAM-10M) label CC BY-SA 4.0. Preserve dataset attribution/license metadata in artifacts. Check actual downloaded row counts: suite README summaries differ from current cards and published result denominators.

Further inspected sources: pinned [LongMemEval runner](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/longmemeval/run.py), [LLM client](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/benchmarks/common/llm_client.py), and [compose](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker-compose.yml). See [research-benchmarks.md](research-benchmarks.md) for supporting research and caveats.
