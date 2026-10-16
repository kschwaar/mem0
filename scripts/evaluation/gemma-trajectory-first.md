# First real Gemma SDK trajectory — diagnostic, not acceptance

Executed on 2026-10-07, approximately 17:34:59–17:36:42 UTC, against this checkout
at `e398105aacfda8aa09c238f3be8ca23466a9a9e9` with uncommitted adapter/evaluation
changes. Runtime: Python 3.11.15, oMLX 0.7.0, M1 Pro/32 GiB. Exact generation model
`gemma-4-12B-it-4bit`; embeddings `Qwen3-Embedding-0.6B-4bit-DWQ`, 1024 dimensions.
No personal/service memories, persistent oMLX settings or default model changed.
Ministral was unloaded before Gemma loaded, retaining the embedding model.

## Authoritative artifacts

Local artifact directory: `/tmp/mem0-gemma-trajectory.hh757h`.

- `execute.py`: real Memory factory, evaluation-only SDK wrappers and resource gates.
- `provider-evidence.jsonl`: synthetic prompts, raw model responses, all returned
  usage/phases; embedding inputs/dimensions/finite checks, not raw vectors.
- `resources.jsonl`: timestamped status/pressure/swap snapshots around provider calls.
- `model-actions.jsonl`: authorized generation-model unload result.
- `run/manifest.json`, `run/outcomes.jsonl`, `run/traces.jsonl`, `run/summary.json`:
  runner provenance, all nine outputs/checks and adapter spans.
- `run/qdrant` and `run/history.sqlite`: fresh isolated stores retained for audit.

Root trace ID `7595dbe809c84db49fa6dad68ff4d510`.
Dataset SHA-256 (runner canonical JSON):
`1650f82dc23ba3c386b21cc24d39605dccd9f053e707837474fa718c8cca82d4`.
Config SHA-256 (runner canonical JSON):
`2282f6285d4b430408c9c8dc89e2818e6f8bdd8c61ba11b17933732e2045e1de`.

Important evidence-label limitation: the runner labels any non-default factory
`test_double`. This run supplied a wrapper factory that called its unchanged
`make_memory` and verified import from this checkout, then instrumented the real
SDK clients. It did not supply a fake Memory implementation. Do not alter the
manifest retroactively; inspect `execute.py` and raw provider evidence to resolve
the label. Before immutable benchmark acceptance, snapshot all modified source
and give wrapped live factories an explicit, verified provenance path.

## Result

Nine of nine operations completed, eleven of eleven read-state phrase checks
passed, zero operation/ungradable/cleanup errors. Three generation calls and
twelve embedding HTTP calls were observed; no SDK retries configured. Distinct
synthetic scoped users were prefixed with the run UUID.

| Operation | Observed state/decision | Client operation time |
|---|---|---:|
| Initial preference add | ADD: user's preferred drink is coffee | 91.096 s |
| Initial recall | coffee retrieved | 0.181 s |
| Second user's add | ADD: other user's preferred drink is cocoa | 6.399 s |
| First user's correction | UPDATE same memory ID to tea, previous coffee retained in action evidence | 7.256 s |
| Corrected-state listing | tea only; coffee/cocoa excluded | 0.00045 s |
| Unseen user's search | empty results | 0.151 s |
| Memory/store reload | completed | 0.031 s |
| Post-reload recall | tea retained; coffee/cocoa excluded | 0.273 s |
| Second user's listing | cocoa retained; tea/coffee excluded | 0.00019 s |

Raw generated action objects were parseable JSON, exact model identity,
finish_reason stop, with no observed reasoning leakage. The actual runtime
contract is `memory` action objects, not the direct diagnostic's `facts` array.
The three actions were two ADDs and one UPDATE. DELETE/NONE and third-party
attribution under the actual long prompt were not tested.

## Performance and runtime controls

The first generation request contained **8547 prompt tokens**, took 81.872 s
at the SDK-call boundary, and reported model load 5.67 s, TTFT/prefill 72.89 s,
generation 2.69 s and 44 output tokens. First-add operation time includes
initialization, embeddings and local storage work as well. Later correction
reported 8590 prompt tokens with 8192 cached, TTFT 4.07 s and generation 2.90 s.
These observations reveal substantial static prompt cost and prefix-cache
dependence. They are not warm/cold percentiles or evidence of sustained speed.

Actual outbound adapter parameters included thinking false, temperature zero,
top_p one, max_tokens 256 and json_object. The new OpenAI config uses a 30-second
SDK timeout and zero retries; embedding client used equivalent process-local
SDK options. **The first request nevertheless lasted 81.872 seconds.** An SDK
timeout is not a whole-call wall deadline; this run does not prove cancellation
or a 30-second total bound. Five-minute dispatch budget and provider-count caps
were separate gates. A strict total request deadline remains required before
larger ingestion, with incomplete calls reconciled rather than auto-retried.

Execution also emitted a Hugging Face `Fetching 18 files` message. Qdrant's
installed BM25 sparse encoder lazily uses FastEmbed `Qdrant/bm25`; incidental
asset fetching was not independently traced/pinned in this run. Do not claim
zero network asset fetches or a fully hermetic setup. Inventory/pre-cache or
explicitly block ancillary downloads before subsequent reproducible runs.
No generation checkpoint was downloaded by the coordinator.

## Resource snapshots and coverage limits

Tracked pool memory after run: 7,430,153,079 bytes (Gemma plus embedding).
Final running ceiling: 15,285,197,928 bytes. Final pressure-query free metric:
56%. Swap used stayed 1936.44 MiB at recorded before/after points. No configured
resource stop was encountered. Pool accounting and pressure-query percentages
are not physical peak/free RAM measurements; no net swap change does not mean
no swap I/O. Measurements are sequential, not atomic.

Resource gates: idle server, only selected generation model plus embedding;
tracked memory <90% ceiling, pressure-query metric >=20%, net swap growth <=64
MiB, eight generation/twenty-four embedding-call caps and five-minute dispatch
budget. These are conservative diagnostics, not desktop acceptance targets.
Docker/normal apps and responsiveness were not verified. No Langfuse trace is
claimed; local adapter/SDK evidence is retained independently of observability.

Fresh embedded Qdrant is not the actual service HTTP/auth/container/store path.
Reload reopens Memory/stores in one process, not an OS/service restart. Phrase
checks prove these particular state assertions, not a 99.5% reliability goal,
comprehensive factuality, long-history retrieval, or cross-tenant acceptance.

## Next gates

1. Normal desktop/Docker resource baseline and linked Python Langfuse readback.
2. Fresh held-out attribution and ADD/UPDATE/DELETE/NONE cases with reviewed labels.
3. Harden evidence provenance, strict dispatch/deadline handling and ancillary
   asset inventory; wire exact-fork benchmark wrapper and checkpoint failures.
4. Inventory revision-pinned dataset; select one eligible complete history only
   after estimating ingestion work from the observed long prompt/cache behavior.
5. Broader held-out quality and actual service acceptance. No full-suite launch,
   production-default switch or aggressive memory policy follows this smoke.
