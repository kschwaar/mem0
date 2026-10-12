# oMLX baseline readiness

Original extraction model: `Qwen3.8-27B-4bit` (historical memory-gated candidate).
Native OpenAI-compatible URL: `http://127.0.0.1:8080/v1`.
Container route: `http://host.docker.internal:8080/v1`, to verify from the
isolated evaluation container before inference. Runtime is oMLX 0.7.0.

## Latest result — 2026-10-07

The initial gate below is historical. Authenticated admin previews established
that the balanced dynamic component, not static or Metal, bound the roughly
9.86 GiB ceiling. Quitting Docker increased it to approximately 16.6 GiB, still
leaving inadequate inference/desktop margin for the 15.70 GiB 27B estimate.
The user selected smaller comparison models rather than loosening guards.

Gemma `gemma-4-12B-it-4bit` passed direct extraction diagnostics and a real
isolated SDK trajectory; Qwen3 14B remains a baseline with an attribution failure.
See [the trajectory report](gemma-trajectory-first.md). After that run, Gemma
and the embedding model were loaded, pool model memory 7,430,153,079 bytes,
running ceiling 15,285,197,928 bytes, pressure-query metric 56%, swap used
1936.44 MiB with no net change across snapshots. These readings are not physical
peak memory or normal desktop/Docker acceptance. Re-probe before new inference.

Use [Gemma's config](gemma-omlx-python.json) for the experimental candidate:
thinking false via backend JSON, temperature zero, bounded output, explicit SDK
timeout and zero retries. Embedding API dimensions remain omitted; the fresh
Qdrant collection uses the confirmed 1024 dimensions. Do not infer full tracing
from direct HTTP or local ledgers: Langfuse readback is a separate unfinished gate.

Read-only metadata checks on 2026-10-07 confirmed the exact model ID, healthy
server and four discovered models. API authentication uses the existing
`OMLX_API_KEY` environment variable; its value was never printed or written.
The initial probes made no inference or model loading calls. Do not use the server's default
distilled model implicitly: each test must send the selected model explicitly.

## Resource evidence and current gate

| Observation                      | Value                                                                     |
| -------------------------------- | ------------------------------------------------------------------------- |
| Host                             | M1 Pro, 10 CPU cores, 32 GiB RAM                                          |
| Available disk                   | Approximately 252 GiB                                                     |
| Swap occupancy                   | Approximately 1.91 GiB; snapshot, not new evaluation growth               |
| Docker VM memory                 | Approximately 7.75 GiB                                                    |
| Running Langfuse stack           | Six containers, approximately 4.3 GiB combined                            |
| oMLX loaded models               | Zero at probe time                                                        |
| oMLX final ceiling               | 9,624,028,168 bytes, approximately 8.96 GiB; dynamic snapshot             |
| Selected model resident estimate | 16,857,268,416 bytes, approximately 15.70 GiB                             |
| Discovered embedding model       | `Qwen3-Embedding-0.6B-4bit-DWQ`, approximately 0.33 GiB resident estimate |

The selected model exceeds the observed admission ceiling. Installed oMLX
`engine_pool.py` checks admission size against the current ceiling and raises
`ModelTooLargeError` when the model alone exceeds it. Source verification and
metadata are enough to fail this readiness gate without loading 16 GiB of
weights. Do not raise or disable memory guards automatically, substitute a
different extraction model, or start the benchmark anyway.

Next resolve whether the ceiling is constrained by configured limits or current
host headroom. Preserve desktop and Docker headroom; model weights alone do not
include all generation/KV/runtime costs. Re-probe after a deliberate resource
change, and require the selected model plus embedding and inference overhead to
fit before one bounded extraction. Existing swap occupancy is a baseline; stop
on sustained new growth or elevated memory pressure during the experiment.

## Prepared configuration and staged execution

### Single embedding smoke completed

One direct HTTP request to `/v1/embeddings` used
`Qwen3-Embedding-0.6B-4bit-DWQ` with synthetic text `User prefers tea.`.
It returned the same model ID, one vector at index zero, **1,024 finite values**,
L2 norm approximately 0.9989, reported five prompt/total tokens and elapsed time
approximately **1.23 seconds**, including any load work. No repeat, extraction,
reranking or judge call was made. The probe had a 30-second HTTP timeout and a
256 KiB response bound. Raw vectors and credentials were not retained.
This is direct endpoint shape evidence, not SDK integration or retrieval quality.

Afterward oMLX remained healthy with one loaded embedding model and a pool
memory estimate of 352,061,593 bytes. Swap occupancy remained 1,960.44 MiB;
the macOS memory-pressure query reported 76% system-wide free percentage.
The dynamic ceiling increased to approximately 9.67 GiB, still below the
selected extraction model's 15.70 GiB resident estimate.

[`qwen-omlx-python.json`](qwen-omlx-python.json) selects the exact user-requested
model and the discovered embedding model. It contains no credential. The OpenAI
provider resolves `OPENAI_API_KEY` from the environment: map the existing
`OMLX_API_KEY` to that variable only for the evaluation process. Ensure
`OPENROUTER_API_KEY` is absent, because the Python adapter gives it precedence.
No external provider URL is needed.

The embedding configuration intentionally omits `embedding_dims` so the adapter
does not send a dimensions override to oMLX. The single smoke confirmed actual
output dimensions of 1024; use that value for the isolated vector store. This file is a focused
adapter configuration, not a complete trajectory/server storage configuration.

Keep the first extraction output cap at 256 tokens, concurrency one and one
repetition. Thinking is enabled/preserved in the current oMLX model metadata;
a token-limited response may contain no complete final JSON. Classify that as
incomplete output, then deliberately compare a bounded non-thinking profile or
a larger output cap after resource review. Never equate an output cap with a
total server memory or wall-time limit.

After the memory gate passes: one extraction, inspect its result,
then the tiny authored correction/isolation/reload trajectory with fresh storage.
Only then inventory and select one complete LongMemEval history that fits the
pilot budget. Follow the [resource-aware ramp](benchmark-integration.md).

## Dataset pilot research

The data reviewer identified cleaned S revision
`98d7416c24c778c2fee6e6f3006e7a073259d48f`, file
`longmemeval_s_cleaned.json`, published size 277,383,467 bytes and SHA-256
`d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`.
Verify these against downloaded bytes; no dataset was downloaded or inventoried.
Select the smallest eligible complete history deterministically by ingestion
pairs, input bytes and question ID, after grouped split assignment. Exact pilot
IDs remain unknown until inventory. Supply the selected subset with
`--dataset-path` and `--all-questions`; do not rely on the runner's default sample.

Sources: [dataset publisher commit](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/commit/98d7416c24c778c2fee6e6f3006e7a073259d48f),
[oMLX project](https://github.com/jundot/omlx), installed local oMLX source and
authenticated metadata endpoints. Public upstream source can differ from the
installed version, so admission behavior was inspected locally.
