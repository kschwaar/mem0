# AI evaluation workbench

This workbench tests the actual downstream checkout. Start with the TypeScript
SDK, then use the Python companion for its distinct contracts and stateful smoke
tests. No live model measurements have been made during setup. Offline results
verify the runner and fixtures; they do not establish Qwen quality or speed.

## What the repository calls

The [inventory](research-inventory.md) covers generation, extraction, vision,
procedural summaries, relationship extraction, embeddings, reranking, hosted
memory services, integrations and sample applications. Python and TypeScript
have different extraction contracts: Python proposes memory actions, while the
current TypeScript pipeline adds extracted facts. TypeScript vision creates an
independent OpenAI client. Reranking has its own model configuration. A local
extraction model therefore does not establish that all processing is local.

Parsing errors can become empty successful results and reranker failures can
become fallback scores. Measure transport success, contract validity and quality
separately. Existing mocked SDK tests do not measure live-model effectiveness.

## TypeScript first

From `mem0-ts/`, use the package's existing pnpm toolchain. The runner imports
this checkout's adapters and renders this checkout's TypeScript extraction
prompt. It does not call the published npm package.

```bash
corepack pnpm exec ts-node --compiler-options '{"module":"CommonJS","rootDir":"."}' \
  scripts/evaluate_ai_calls.ts --offline \
  --cases ../scripts/evaluation/cases.jsonl \
  --out /tmp/mem0-ts-offline-unique --repeats 3 --concurrency 2
```

Use a new output directory on every run. Inspect `--help` for supported flags.
Unsupported stages are recorded as skipped, with coverage visible separately
from correctness. Python relationship/action fixtures are not evidence that the
TypeScript SDK supports those contracts.

For live testing, copy [qwen-ollama-ts.json](qwen-ollama-ts.json) or
[qwen-vllm-ts.json](qwen-vllm-ts.json) to a local configuration and replace the
model placeholders with exact installed/served IDs. Confirm the embedding
model's dimensions and use its exact tag. Configure the nested reranker model
explicitly. The vLLM example uses a local placeholder API key; obtain actual
credentials through the environment when needed, and keep them out of git.

```bash
corepack pnpm exec ts-node --compiler-options '{"module":"CommonJS","rootDir":"."}' \
  scripts/evaluate_ai_calls.ts \
  --config /absolute/path/to/local-config.json \
  --cases ../scripts/evaluation/cases.jsonl \
  --out /tmp/mem0-ts-live-unique --repeats 5 --concurrency 1
```

The SDK's Ollama implementation can pull missing models and does not apply all
sampling configuration fields. The runner's readiness checks must pass before
constructing these adapters. Do not infer temperature control from a config
field alone. Begin at concurrency one; repeat separate runs at two, four and
eight once readiness and quality pass. Adapter latency includes its internal
work; it is not GPU-only generation latency or time to first token. A latency
gate checks completed work and does not cancel requests. Use serving/runtime
timeouts and an external wall-time budget where the adapter does not provide
cancellation.

## Outputs and measurement

The run manifest identifies source/config/dataset hashes and safe model
metadata. Each trial remains in a local JSONL ledger, including failures. The
summary reports counts, rates, latency percentiles and per-case/task breakdowns.
Inspect individual failures before accepting aggregate scores. Unknown token
usage, hidden retries and unobserved SDK calls must remain unknown, rather than
being recorded as zero. Provider-specific tracing coverage is described by the
runner and the [tracing research](research-tracing.md).

The TypeScript runner links case/call IDs to observed SDK spans and captures
reported token usage before adapter normalization for supported OpenAI/Ollama
client methods. Ollama durations retain their native nanosecond unit. These
hooks are local to the evaluation process, do not instrument production, and
cannot see HTTP attempts hidden within an SDK. Its JSONL ledger is synchronized
after each completion; unfinished calls have no outcome and must be identified
against the planned case/repetition count. Aggregate throughput includes local
grading and ledger writes, so inspect SDK and adapter timings for model speed.

For focused experiments, add `--task llm`, `--task embedding`, or `--task rerank`
to the TypeScript command. `--case extract.preference` selects one fixture.
Live gates accept `--min-pass-rate 0.95 --max-p95-ms 5000`; choose thresholds for
the selected workload rather than mixing chat, embedding and reranking latency.
The summary exposes skipped coverage separately. Offline runs disable model
quality gates and label all evidence synthetic.

The Python focused runner retains synthetic outputs locally; TypeScript stores hashes by default and enables content with `--record-content`. Real conversations and
model outputs can contain private information; content capture is not a
credential-redaction guarantee. No trace UI or external analytics service is
required for the workbench. The local ledger is independent of sampled PostHog
usage analytics. The TypeScript runner now supports opt-in real OpenTelemetry
export and durable evaluation scores to localhost Langfuse. See the
[Langfuse setup and verification guide](langfuse.md).

Schema and keyword checks are contract checks and limited semantic proxies.
They are not a comprehensive factual judge. Extend fixtures with reviewed
expected facts/actions and negative expectations. Audit uncertain results with
humans or a fixed independent judge; keep that judge separate from the model
being tested. Maintain held-out cases and version any learned regression cases.
Repeated trials of a tiny synthetic dataset do not provide a population-quality
estimate even when a Wilson interval is reported.

## Python companion and stateful checks

The focused companion needs only Python's standard library in offline mode:

```bash
python3 scripts/evaluate_ai_calls.py --offline \
  --cases scripts/evaluation/cases.jsonl \
  --output-dir /tmp/mem0-python-offline-unique --repeat 3 --concurrency 3
```

Live runs add `--config /path/to/config.json` and remove `--offline`. They use
the local Python factories and require the selected provider dependencies.
[qwen-ollama-python.json](qwen-ollama-python.json) is an independent Python
configuration template. The shared embedding fixtures use deliberately tiny
synthetic vectors; update expected dimensions to the real model for Python
live embedding tests. Never interpret a shape check as retrieval quality.

The [trajectory runner](../evaluate_memory_trajectory.py) is a Python-only
baseline for sequential correction, user separation and persistence after
reload. It forces fresh embedded Qdrant and history storage beneath the output
directory and rejects remote stores and graph configurations. It never resets
an existing store.

```bash
python3 scripts/evaluate_memory_trajectory.py \
  --config /path/to/python-model-config.json \
  --output /tmp/mem0-trajectory-unique
```

This calls configured models. Read expectations and adapter spans are saved
locally; raw outputs require `--retain-outputs`. Phrase checks and a short
trajectory do not establish long-term effectiveness or simulated aging.
TypeScript end-to-end trajectories, graph/vision/procedural cases and hosted
service internal traces remain additional coverage work.

## Long-term effectiveness and official benchmarks

Reuse [Mem0's official memory benchmarks](research-benchmarks.md) for LOCOMO,
LongMemEval and BEAM. The existing `evaluation/` submodule is preserved. The
[integration recipe](benchmark-integration.md) identifies the pinned revision,
an exact-fork build and necessary wrapper API adaptations. Stock benchmark
Docker installs another upstream branch; running it unchanged would measure a
different implementation. The documented wrapper adaptations have not yet
been implemented or executed.

Keep extraction model, embeddings, retrieval settings, answerer, judge, data
slice and hardware explicit. Change one variable at a time, pin revisions and
retain actual selected IDs. Start with a small LongMemEval slice, then expand
after measuring ingestion work. Add delayed recall, correction, distractors,
state reload and scope checks at multiple memory-store sizes. Dataset licenses
and the substantial work required for BEAM are documented in the research.

## Research workspace and validation

Three research agents produced the inventory, benchmark and tracing reports.
The cmux workspace has a research pane with inventory/benchmark tabs and a
separate tracing pane. Those views are reports; the delegated agents themselves
are managed by the agent collaboration runtime.

Focused tests cover malformed model results, missing/invalid expectations,
failure denominators, vector shapes, config privacy, latency gates, isolated
storage and reload. Run the affected Python tests and Ruff, and the TypeScript
runner tests, script typecheck and Prettier before changing the workbench. The package currently has no `typecheck` script;
use `corepack pnpm exec tsc --noEmit`. Its existing package-wide failures in Jest
helpers and community LangChain integration must be reported separately from
the new runner checks. Node 26 is installed here; CI normally uses Node 20/22.
No CI workflows, core SDK contracts or evaluation submodule pins are changed.
