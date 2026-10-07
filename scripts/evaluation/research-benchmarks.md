# Mem0 benchmark research

Recommendation: reuse the official memory-benchmarks project for longitudinal, end-to-end memory quality, and construct a small complementary harness for individual AI-call contracts, repeatability, and load. Do not treat a QA benchmark score as evidence that every extraction, reconciliation, graph, embedding, reranking, or multimodal call works.

## Local state

Read-only review on personal/main. `evaluation` is an uninitialized submodule pinned to `4b61c5d31b9c668a12b4f5e78064248a02c82d2b`; no initialization or repository changes performed. CodeGraph was consulted before code search. The pinned README was accessible remotely.

## Supported reuse

The official [suite](https://github.com/mem0ai/memory-benchmarks) supports Mem0 OSS and Cloud, ingestion/search/evaluation stages, resumable runs, saved result evaluation, multiple retrieval cutoffs, and a comparison UI. Python CLI is sufficient; frontend is optional. It includes LOCOMO, LongMemEval, and BEAM. It is Apache-2.0 code. Cloud needs hosted credentials; OSS uses Docker plus Qdrant. Changing one model while holding embeddings, retrieval, answerer, and judge constant is the correct comparison design. These are memory QA benchmarks, not a general provider-call load-test system.

Critical caveat: [pinned Docker requirements](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/4b61c5d31b9c668a12b4f5e78064248a02c82d2b/docker/mem0/requirements.txt) install `mem0ai` from upstream `feat/v3-pipeline`, rather than this fork. [Dockerfile](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/docker/mem0/Dockerfile) also modifies installed embedding config to permit SageMaker. Stock deployment would evaluate a different product. Adapt the server image or client to use the exact local fork commit; record both repository SHAs and the installed package path/version.

## Dataset coverage

| Dataset | Useful coverage | Caveat |
|---|---|---|
| LOCOMO | Ten conversations; annotated evidence, factual recall, multi-hop and temporal QA | Small conversation sample; README question count conflicts with displayed result denominator, so report actual selected question IDs/count |
| LongMemEval cleaned | 500 questions; knowledge updates, temporal reasoning, multi-session, preferences, abstention | Each question has its own history; full run ingestion is substantial |
| BEAM | 100K–10M token histories; contradiction resolution, ordering, preferences, instructions, abstention, summaries | Large cost/time; start with one 100K conversation; current HF counts differ from suite README |

[LOCOMO source](https://github.com/snap-research/locomo) provides evidence dialogue IDs and event annotations. Its [license](https://raw.githubusercontent.com/snap-research/locomo/main/LICENSE.txt) is CC BY-NC 4.0, not Apache; retain attribution and do not assume benchmark code's license covers datasets. [LongMemEval](https://github.com/xiaowu0162/LongMemEval) provides session and turn evidence, oracle and longer histories. Its [cleaned dataset card](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) labels MIT. Pin the cleaned revision/hash because original histories changed in 2025. LongMemEval-V2 exists but is not one of the three supported suite runners. Check [BEAM](https://huggingface.co/datasets/Mohammadta/BEAM) and [BEAM-10M](https://huggingface.co/datasets/Mohammadta/BEAM-10M) cards independently before redistributing data.

BEAM and BEAM-10M cards both declare CC BY-SA 4.0. The current cards show 90 rows in BEAM (100K has 20 rows) plus 10 rows in BEAM-10M, rather than the suite README's claim of 100 conversations per bucket. Record actual downloaded rows and hashes rather than trusting summary counts.

## Qwen support and remaining gaps

The [Ollama config](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/configs/ollama.yaml) provides local extraction and embedding settings, with Docker Desktop host routing. Substitute an exact installed Qwen model tag for extraction; choose the embedding model separately and verify dimensions. Local extraction does not make answerer/judge local automatically.

[LLMClient](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/benchmarks/common/llm_client.py) accepts OpenAI-compatible `base_url` (e.g. local serving), applies retries/rate limiting, and supports JSON output. It returns strings/dicts rather than provider response metadata. Exhausted generation can become empty strings/dicts, which obscures distinction between provider failures and model-quality failures. Record each attempt, raw failure, latency, usage, and final parsing separately in the focused harness. [LongMemEval runner](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/benchmarks/longmemeval/run.py) creates answerer/judge without explicit base URLs; separate endpoint CLI controls would be useful. It records search latency and supports retrieval-only judging, which helps isolate answer generation from memory quality.

[Metrics](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/benchmarks/common/metrics.py) supplies accuracy, mean score, groups, cutoffs, errors, and Kendall tau-b. [BEAM runner](https://raw.githubusercontent.com/mem0ai/memory-benchmarks/main/benchmarks/beam/run.py) adds rubric nugget scores and ordering alignment calls. Those extra judges increase run costs; count all evaluation calls separately from product calls. No credible fixed cost estimate is possible until exact model, history, cutoff, token usage, and selected dataset slice are specified.

## Concrete implementation recommendation (inference/design)

1. First build a no-paid-call dry-run harness with explicit case IDs and task labels: fact extraction, add/update/delete/no-op decision, entity/relation extraction, contradiction deletion, image/audio understanding, embedding and reranking.
2. Require task-specific checks: JSON/schema validity, allowed tool names/arguments, fact precision/recall, correct mutation/no-op decisions, valid finite embedding dimensions, ranking relevance. Use human-authored expected outputs and deterministic checks before optional judges.
3. Run repeated trials per case/model; publish first-attempt success and eventual success separately. Include latency p50/p95/p99, throughput, timeouts, retries, tokens, failure categories, and paired quality deltas with uncertainty. Distinguish cold and warm local inference and sweep concurrency with bounded budgets.
4. Keep answerer/judge fixed for extraction comparisons; retain judge prompts/version and audit a sample of judgments manually. Use oracle retrieval to identify answerer failure versus retrieval failure.
5. Add chronological sequences with corrections, conflicting facts, old preferences, and delayed queries. Compare memory state after each step and later retrieval, including this fork's intentional forgetting/decay behavior. Benchmark QA alone will not isolate those semantics.
6. Next integrate a fixed LongMemEval slice and LOCOMO where dataset use is appropriate; then one BEAM 100K conversation; scale only after recorded budget estimates.

All runs should save exact model tag/digest, quantization, serving engine/version, hardware, context window, sampling parameters, embedding config, prompt hashes, case/dataset hashes, repo SHA, provider endpoints with credentials removed, timings, traces, outputs, and seed. Artifact reuse needs a config hash to prevent checkpoint contamination across models.

This report is research and design, not an executed model performance measurement.
