# Active goal: focused localhost coding-memory assessment

The user selected **option 2** on 2026-10-14. This replaces the previous
fix-everything-before-benchmarking agenda. Measure one frozen local setup against
the existing fictional coding fixture; record failures rather than launching
another repair or model-selection campaign.

## Scope and budget

- Actual isolated product API: `http://127.0.0.1:8898`, backed by localhost oMLX.
- Freeze the current Ministral 8B extraction profile, Qwen3 embedding profile,
  source hashes, and configuration before dispatch; do not tune them mid-run.
- Preserve the existing fixture: 8 adds, 16 searches, 6 inventory reads,
  two fresh synthetic owner scopes, and top-k 10.
- One serial run, no automatic retries, no answerer or judge calls, and a
  1,200-second process cap. Stop on unresolved timeout, authentication failure,
  configuration drift, or deadline; explicitly record unattempted cases.
- Completed request failures remain results. Do not hide them by narrowing
  denominators or rerunning only failed cases.
- At most two GPT-6-Luna workers concurrently; only the supervisor dispatches
  local inference. Close owned worker surfaces after collecting their evidence.

## Breakdown and completion criteria

1. Freeze actual service/model configuration, fixture, source, and empty scopes.
2. Prepare the existing coding runner and independently freeze review criteria.
3. Run once through the existing product client and save each operation.
4. Review all 13 supported queries for complete source-grounded top-5/top-10
   coverage, correct project/path ownership, current-versus-retired facts,
   preferences, rationale, and caveats. Review unsupported-fact contamination
   and both positive and negative owner-scope controls.
5. Report add/search errors, missing cases, latency, inventory inaccuracies,
   limitations, a suitability verdict, and a reproducible invocation.

An assessment is complete when the planned cases and outcomes reconcile and the
verdict is supported by saved evidence, not when every query passes.
Evidence goes under `artifacts/evaluation/coding-focused-20261014/`.

## Completed outcome

The single frozen assessment completed with no operation errors or retries.
Independent and supervisor source-grounded reviews agreed: correct fact bundles
were retrieved, but one stored path was misspelled and conflicted with correct
evidence on two path queries. The measured profile is useful for advisory
retrieval, not authoritative exact-identifier recall. No repair or repeat run is
automatically queued.

Results, timings, diagnostics, limitations, and reproduction instructions:
[focused assessment report](../../artifacts/evaluation/coding-focused-20261014/report.md).


## Explicit exclusions and limits

No full LoCoMo, LongMemEval, or BEAM suite; no full-history replay, model sweep,
prompt tuning, oMLX backend repair, automated judge calibration, or lifecycle-tool
development. Langfuse is diagnostic only. The fixture does not prove answer-level
abstention, ordinary-key tenant authorization, restart durability, real stored
old-to-new transitions, or assistant/tool-transcript fidelity. No full-suite
scores or blanket production-readiness claim may be inferred from this run.

## Historical material — superseded scope

The remaining notes describe earlier attempts and authorizations, not active
tasks. Preserve their failures and raw scores; do not resume those workflows
without a new user decision. Historical handoff:
[CHECKPOINT-2026-10-07.md](CHECKPOINT-2026-10-07.md).

Intended use: coding-agent memory of repository facts, decisions and prior work.
User authorized continued bounded experiments on2026-10-09. The coding diagnostic
completed6adds/8searches: all7supported questions had required top10 evidence.
Three supplementary Gemma answers handled corrections, unsupported recall and
uncertainty correctly. These are synthetic agent-reviewed probes, not broad
accuracy. See `artifacts/evaluation/usefulness-next-v1/coding-results-summary.md`.

An API restart exposed an evaluation-capture startup defect. The versioned
per-boot log repair passed18offline tests andRuff, preserved old evidence,
and restored the same image/environment/volumes. A subsequent clean API restart
preserved all10coding memories exactly. PostgreSQL/Qdrant/host durability is
not established. Evidence: `artifacts/evaluation/capture-restart-v2` and
`artifacts/evaluation/usefulness-next-v1/persistence-002`.

Use the pinned mem0ai/memory-benchmarks suite as the primary quality evaluation
workflow against an isolated Dockerized copy of this fork's actual Mem0 service.
Mem0 uses host oMLX for Gemma extraction and Qwen3-Embedding embeddings. Keep
Dockerized Langfuse available for runtime/experiment diagnostics; trace export
and project credentials must be verified before claiming Langfuse integration.

## Deliverables and completion evidence

1. One documented, repeatable benchmark invocation path through the actual
   authenticated product API, using the existing adapter rather than another
   Mem0 wrapper. Verify effective models/configuration, isolated storage,
   request translation, and visible failure handling.
2. A complete, revision-pinned LongMemEval history processed serially, with an
   explicit approved workload budget, reconciled ingestion, and saved top-k-10
   retrieval output. Partial or truncated histories are not benchmark completion.
3. Retrieval-sufficiency scoring and answerer-mode scoring of the saved output,
   with explicitly configured evaluation endpoints/models, reviewed initial
   judgments, errors/coverage, latency, and question-level evidence. Reuse suite
   scoring; do not invent extraction gold from QA answers.
4. A fixed small expanded slice covering the six LongMemEval question types and
   abstention, with descriptive results and declared limitations. Freeze judge,
   embedder and retrieval settings for extractor comparisons. Do not claim
   independent held-out acceptance from the current overlapping source histories.
5. An accessible permanent baseline report and rerun instructions, including
   minimal auth/isolation/unintended-mutation/persistence regressions. If
   Langfuse credentials are available, verify linked trace export/readback;
   otherwise explicitly document that unverified optional capability.

Start small, keep one inference experiment/worker at a time, and ramp coverage
after the preceding stage reconciles. Increased inference budgets require explicit
approval. Do not mutate personal stores or persistent model/service defaults.
Keep credentials out of reports, commands and version control.

## Not on the critical path

No second benchmark server, new evaluation framework, TypeScript deployment
work, exhaustive component labeling, production dataset acquisition, full suite,
BEAM stress runs, or further hardware investigation without observed distress.
LOCOMO is a useful next extension after the initial baseline, not a prerequisite.
Use captured failures to justify targeted regression datasets and Langfuse-led
optimization later. Numerical acceptance thresholds remain proposed, not achieved.

## Historical checkpoint — superseded

The material below records earlier benchmark authorizations and execution state.
It is retained as provenance, not an active goal or authorization to run the
listed workflows. The current bounded goal and its completed outcome are above.

The first approved pilot stopped after nine checkpointed adds when the outer
30-second HTTP request timed out; the server wrote afterward. That original
scope is preserved and was never replayed. The separately prepared v2 runner
uses an explicit run-scoped 120-second HTTP timeout and unchanged provider
settings. Its 50 model-free tests passed. The user approved a fresh 213-add,
one-search run with no retries/scoring and a 7200-second cap.

Permanent completed run: `artifacts/evaluation/runs/longmemeval-http-budget-v2-fresh-001`
with sibling approval, manifest, control, console, and reconciliation evidence.
It processed the complete selected history `5831f84d` (40 sessions, 485811 source
characters) and saved retrieval. The independent lineage audit passed. The first three
scoring calls completed without retries: retrieval PASS, but answerer judgment
hit a prompt/parser format conflict. Independent review confirmed it. The
versioned prompt-only repair passed three offline tests. A separately approved
two-call answerer run then returned bare `yes` and recorded PASS, reviewed
against gold15. Both attempts and the original prediction are preserved.
Reviewed evidence: `artifacts/evaluation/scoring-answerer-v2-001/review.md`.
The bounded expansion completed: multi-session case `ba358f49`, 205 serial adds
and one search, reconciled with exact source request hashes and no retries or
HTTP errors. Retrieval top-10 lacked both required facts (age 32 and Rachel's
wedding next year). Two independent reviews of the 258 visible scoped memories
found no supporting text; list completeness and expired-row visibility remain
unproven. The retrieval judge returned valid FAIL. The answerer appropriately
said it lacked information, so answer accuracy against gold 33 is FAIL. The
same-model judge nevertheless returned `yes`; exact reconstructed prompt hash
matches the captured call. Preserve this raw false-positive PASS separately
from reviewed accuracy and appropriate abstention.

Evidence: `artifacts/evaluation/usefulness-next-v1/standard-audit-001`,
`standard-scoring-prep`, `failure-diagnosis-001`, `failure-diagnosis-002`,
`scoring-review-003` and `replay-review-003`. Completed worker tabs were closed.
The original proposed replay launcher required repair. A subsequent bounded
live replay has now run: two unchanged source chunks retain age 32 and Rachel's
2023 wedding, retrieved at ranks two and one. This is not proof that the full
205-add history retains them. The original A05 coding input also now retains
its test and implementation paths, including its production-safety caveat.
The original B01 confidential fixture remained empty in earlier attempts. A
generic caller-selected-scope instruction, preserving disclosure restrictions
with each fact, now passes the unchanged fixture through actual API capture and
retrieval. This does not authorize cross-owner access: the application must
enforce tenant boundaries before calling extraction. Admin-scope filtering is
verified; ordinary-key tenant authorization and raw-history erasure are not.

Authenticated metadata-only Langfuse readback finds all29 HTTP spans and11
generations from `live-005`. Numeric-looking IDs were coerced, including the
hexadecimal-looking `41793e40` becoming a float. Unique matching against known
wire IDs recovered that historical correlation. Prefixed IDs subsequently passed
exact HTTP-to-Langfuse string readback after the isolated source cutover. Timed-out
requests that never deliver their response header still lack a wire ID in the
client ledger. No LoCoMo or BEAM scores exist; no full LongMemEval suite has run.
Current evidence is under `artifacts/evaluation/repair-20261012/`.

The [integration plan](benchmark-integration.md) is historical below its current
scope notice. Detailed source review is retained in permanent local artifacts:
`artifacts/evaluation/runs/MEMORY-BENCHMARKS-REVIEW-2026-10-07.md`.

## Source checkpoint — 2026-10-12

The branch checkpoint separates provider controls, server runtime tracing and
configuration, product API translation/transport, serial ingestion reconciliation,
and bounded supervision/dataset audits. A Python 3.9 constructor regression was
fixed by creating the async lock within the running loop. The affected offline
suite passed 150 tests; the server tests used isolated optional tracing dependencies
and did not prove container startup or live exporter delivery.

Formatting and the compatibility fix change source hashes. Historical manifests
remain immutable evidence of the code used for their runs. Prepared launchers that
pin the previous helper bytes must fail closed until a new preparation is reviewed;
do not rewrite historical hashes merely to resume execution. Local artifacts and
the CodeGraph index remain excluded from version control.
