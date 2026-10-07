# Official benchmark integration with this fork

Status: proposed, reproducible integration recipe. No submodule initialization, dependency installation, Docker build, dataset download, or model benchmark has been executed as part of this research. Commands below are opt-in and can download dependencies or invoke models. This file does not automate them.

Use the official suite for chronological memory QA and the adjacent focused harness for individual call correctness/reliability/load. The existing `evaluation` submodule pins `4b61c5d31b9c668a12b4f5e78064248a02c82d2b`. Keep that gitlink unchanged. An independent temporary checkout is sufficient for experiments.

## Why stock Docker is insufficient

The pinned [server requirements](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker/mem0/requirements.txt) install upstream `feat/v3-pipeline`. They do not evaluate this fork. Build a replacement server image from an archived, committed fork revision and the pinned benchmark wrapper. Omit the stock Dockerfile's SageMaker validator patch unless SageMaker is the explicit experiment; it changes the tested implementation.

The pinned [wrapper](https://github.com/mem0ai/memory-benchmarks/blob/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker/mem0/main.py) accepts `observation_date` but does not apply it to the SDK. Current fork `Memory.add(timestamp=...)` rejects that parameter. Therefore chronological ingestion is not evidence that historical storage timestamps were applied. Before temporal claims, select and record a protocol: preserve dates in input text, or implement and validate a wrapper adapter for a supported fork API. Do not silently rewrite dates or report simulated wall-clock aging as real aging.

**Compatibility gate:** the pinned wrapper also passes top-level entity IDs and `limit` to `search`, and entity IDs to `get_all`; this fork requires `filters={...}` and `top_k`. Before any runner calls, adapt the temporary wrapper's `search` to collect IDs into `filters`, translate `req.limit` to `top_k`, and adapt `get_all` likewise. Remove its `prompt` argument mapping for custom instructions unless a validated fork API accepts it. Add tiny wrapper tests for these translations and retain the patch/hash with the run. The recipe below prepares an image; it does not provide or validate that adapter. Until this gate passes, use `evaluate_memory_trajectory.py` for a runnable direct-SDK smoke baseline.

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

Prediction still makes extraction and embedding calls for all selected histories; six sampled questions can contain many ingestion calls. Estimate message-pair counts beforehand and set an external wall-time/resource budget. This command has no paid endpoint by design; verify routing before launching. Judge evaluation is a separate opt-in run using the same project, dataset scope and flags with `--evaluate-only` instead of `--predict-only`. Do not change the subject model during a checkpoint resume. Keep an independent, fixed judge for Qwen comparisons and manually review representative judgments. Separate judge failures from subject quality failures.

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
