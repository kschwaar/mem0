# Paused checkpoint — localhost benchmark integration

Historical session checkpoint. Work resumed afterward; see
[the active goal](active-goal.md) for current status and reviewed evidence.
The paused state and missing full-history score below describe 2026-10-07.

User explicitly requested pausing and a handoff on 2026-10-07. Resume only at the
user's direction. This document is the session entry point, not authorization to
launch inference. The goal is incomplete; no full-history benchmark score exists.

## Goal

Use pinned `mem0ai/memory-benchmarks` against an isolated Dockerized copy of this
fork's actual authenticated Mem0 service, using local oMLX Gemma extraction and
Qwen3-Embedding embeddings. Complete prediction/scoring/checkpoint integration;
run one complete LongMemEval history under an explicitly approved serial budget;
save top-k-10 retrieval; obtain retrieval-sufficiency and answerer-mode scores
with reviewed initial judgments; expand a fixed small slice covering six question
types and abstention. Deliver permanent question-level evidence, coverage/errors/
latency, rerun instructions and minimal auth/isolation/unintended-mutation/
persistence evidence. Langfuse is optional diagnostic enrichment unless verified
credentials/readback become available. See [active-goal.md](active-goal.md).

No personal-store writes, persistent defaults changes, second Mem0 wrapper or
framework, broad hardware investigation absent distress, commits, pushes or merges.
Partial/truncated histories and tiny smoke checks do not satisfy this goal.

## Exact stopping point

The prepared command was:

```text
.venv/bin/python artifacts/evaluation/service-stack-v1/run_benchmark_pilot.py
```

Its escalation request proposed 213 serial adds and one search, a 3600-second
wall limit, no retries, no scoring, and permanent isolated evidence. The user
interrupted that approval request. It is NOT approved for a subsequent session.

Pause-time checks found no `run_benchmark_pilot` or `benchmarks.longmemeval.run`
process. None of the proposed pilot artifacts exist:
`artifacts/evaluation/runs/longmemeval-pilot-001`, or its `.manifest.json`,
`.control.json`, `.reconciliation.json` siblings. Thus no pilot dispatch is
observed. An initially sandbox-denied process check was rerun with approval;
the successful check returned no matches. Do not claim a pilot ran.

Services were not shut down, models were not unloaded, and stores were not reset.
Recheck live state in a new session rather than treating these observations as
permanent readiness.

## Completed work

- Reviewed pinned suite runners, clients, checkpoint semantics, scoring, Docker
  wrapper and UI defaults; separately browsed live GitHub documentation/source.
  Recommendation: use its existing pipeline with one small product HTTP adapter.
- Downloaded and hash-verified reusable LongMemEval and LOCOMO datasets under
  permanent `artifacts/evaluation/`; originals and licenses retained.
- Prepared an isolated actual-product Docker stack and CPU image, with auth,
  project-specific storage, no personal mounts, loopback API, Gemma and Qwen
  embedding configuration. Earlier tiny actual-service diagnostics exist.
- Added optional Python runtime tracing support; Langfuse export/readback is
  unverified. Nine actual-service Gemma SDK calls were captured in synthetic/
  narrowly source-derived development diagnostics, not a benchmark baseline.
- Product adapter translates authentication/search/date fields, serializes calls,
  disables retries/redirects and retains fsynced request outcome ledgers.
- Prepared LongMemEval overlay supports serial prediction-only without evaluator
  construction, and scoring-only without Mem0 credentials/contact.
- Missing/malformed predictions now fail before evaluation-client construction;
  incomplete/malformed ingestion cannot proceed to prediction/scoring.
- Local evaluator plumbing supplies explicit loopback `/v1`, environment-only
  key, temperature 0, thinking off, bounded tokens/time and zero SDK retries.
  Malformed/empty/truncated structured output and timeouts raise errors. Retrieval
  mode constructs only a judge. Models must still be explicitly chosen for live
  scoring; stock defaults must not be mistaken for oMLX configuration.
- Validated partial-ingestion resume checks contiguous completed chunks against
  successful input/scope hashes and unchanged source/runner/config context.
  Ledger locking prevents concurrent continuation. Actual CLI graceful-stop/
  resume was tested with mock HTTP: no completed chunk replayed.
- Existing wall-time supervisor now supports fresh prediction-only execution
  and reconciles the planned add count, one search, ingestion and saved prediction.
  History bounds are explicit while original small defaults remain unchanged.
- Installed only `aiolimiter==1.2.1` into permanent
  `artifacts/evaluation/environments/benchmark-deps`; `.venv` unchanged. Real
  evaluation-client import passed with that target on the import path.

## Verification evidence and limits

54 combined model-free adapter/transport/CLI/checkpoint/evaluation-client tests
passed before the most recent supervisor extension. The actual CLI partial-resume
test passed separately afterward. Six bounded-supervisor mock HTTP tests passed
after the extension (success, malformed envelope and HTTP 500 in both phases).
Latest affected tracked scripts passed Ruff and `git diff --check`; both overlay
runner copies matched. Do not describe this as a final all-tests run of the latest
tree. Re-run the complete affected checks before live dispatch.

Tiny actual-service evidence: correct preferences, attribution, correction,
retraction, negatives and two source-derived extraction probes, with recorded
qualification/copying/style defects. No representative acceptance or p95 claims.
See permanent `runs/GEMMA-RAW-BASELINE.md`. Some older plans/readmes have stale
status or superseded sequencing; the active goal and this checkpoint take priority.

## Last read-only live observations

- Mem0 container: `mem0-eval-pilot-20261007-mem0-1`, running, OOM flag false,
  API bound `127.0.0.1:8898`; image
  `sha256:53c6ae836fc6b45bc80779a034082ab23fe728832735f27a614dd7e7f842ffab`.
- Evaluation Postgres and Qdrant running, OOM flags false, no host ports published.
- Effective LLM: `gemma-4-12B-it-4bit`, OpenAI-compatible provider,
  `http://host.docker.internal:8080/v1`, temperature 0, top_p 1, max_tokens 256,
  request_timeout 30s, max_retries 0, thinking disabled.
- Embedder: `Qwen3-Embedding-0.6B-4bit-DWQ`, same host endpoint, 1024 dimensions.
  Qdrant collection `eval_memories`, internal host `qdrant:6333`.
- oMLX 0.7.0: status ok, active/waiting requests 0, loaded models 2, loading 0;
  required model IDs present in `/v1/models`. These GETs performed no inference.
- Last-known six Langfuse containers remain untouched. No project credentials
  or authenticated trace readback established. Hardware investigation is off the
  critical path; stop on actual distress.

## First full-history proposal — still requires approval

Source revision `98d7416c24c778c2fee6e6f3006e7a073259d48f`; source SHA-256
`d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`.
Benchmark pin `4b61c5d31b9c668a12b4f5e78064248a02c82d2b`.

Proposed question `5831f84d`, knowledge-update: 213 complete positional chunks,
485811 source characters, largest chunk 4560 characters. The 199-add minimum
history has a 78117-character chunk, so it is not the preferred local pilot.
Selection is workload-based, not based on observed model scores. No distractors
removed or histories truncated. Candidate manifest:
`artifacts/evaluation/runs/full-history-pilot-candidates.json`.

Launcher pins prepared overlay SHA-256
`a1b593e90f17a67e4cca6bc23cb0023bc4f13358efb5e55057eae4ae0d852ab8`, validates
service image/basic model settings and oMLX idle status, creates a unique scope,
and supervises prediction only. It has not been run. Any edits invalidate that pin
and require re-review/re-pinning. 60 minutes is a cap, not a runtime guarantee.
No evaluator inference is included in this proposal.

## Next session: ordered next steps

1. Read repo and nearest AGENTS instructions; inspect dirty state. Use CodeGraph
   before locating code. Preserve all existing changes and `.codegraph`.
2. Re-run affected model-free tests and review the prepared launcher/overlay.
   Verify live stack/model identities and source pins without inference.
3. Resolve the remaining integration cautions below in proportion to this pilot;
   avoid extending another competing evaluation framework.
4. Obtain fresh explicit approval for the 213-add/one-search budget. Only then
   run the prepared launcher. Monitor its actual process handle, record incomplete
   work honestly, stop on provider/service errors or distress, never blind retry.
5. Audit complete ingestion and retrieval. On interruption, inspect control,
   ledger and checkpoint before authorizing resume. Preserve original scope/run-id.
6. Copy saved predictions into separately versioned scoring directories. Run
   one retrieval-sufficiency judgment; inspect it manually. Then answerer-mode
   scoring with explicit chosen models and oMLX endpoint/key. Do not silently reuse
   retrieval-mode cutoff scores as answerer scores; use clean copies/rejudge.
7. Expand a fixed small category/abstention slice, retain failures/coverage/latency,
   finish minimal service safety evidence, and produce rerun/baseline instructions.
   Keep extractor comparisons paired with fixed embedder/judge/retrieval settings.

### Remaining cautions / unfinished work

- Full-history launch and all real scoring are unproven; no benchmark score exists.
- The new prediction supervisor currently handles fresh launches. A resumed CLI
  can use the existing process watchdog with new exclusive control evidence, but
  resumed-session orchestration/reconciliation needs review before use.
- Resume currently fingerprints source, runner and effective service configuration,
  not immutable weights, image, every adapter/helper or unchanged stored memories.
  Verify these independently; strengthen provenance where necessary. Complete-
  ingestion/search-stage resume is deliberately refused, not implemented.
- Partial/ambiguous server writes cannot be retried just because the client stopped.
- The product parser can normalize malformed model output to empty extraction;
  HTTP success and completed ingestion are not proof of extraction quality.
  Current 256-token output and 30s generation timeout are baseline settings, not
  established suitable limits for realistic histories. Failures are evidence;
  do not change persistent defaults to hide them.
- Evaluator client strict handling/settings have mock coverage, but no real local
  judging calibration, raw usage/phase evidence or fully versioned scoring policy.
  Validate malformed verdicts and preserve evaluator errors distinctly from FAIL.
- Current LongMemEval histories overlap heavily: descriptive development results,
  not a strict independent tuning/acceptance split. LOCOMO license is CC BY-NC 4.0.
- Langfuse credentials remain missing; do not block the initial benchmark for them.
  Retention policy questions/numerical acceptance targets remain unapproved and
  are not prerequisites for descriptive suite results.

## Files, credentials and recovery

Repo: `/Users/kevinschwaar/workspace/mem0`; branch `feature/ai-call-evaluation`.
Large dirty worktree is intentional; no commits/pushes/merges were made. Product
code changes include local OpenAI configuration and Python tracing; they are not
the same as the gitignored prepared benchmark overlay.

Tracked/untracked repo utilities: `scripts/benchmark_{compat,service_requests,
service_client,async_backend}.py`, `scripts/run_bounded_trajectory.py`, associated
tests, `scripts/evaluation/active-goal.md`, `server/runtime_tracing.py` and docs.
Permanent prepared suite: `artifacts/evaluation/adaptations/longmemeval-checkpoint-v1/`;
pristine pin: `artifacts/evaluation/memory-benchmarks/`; datasets, environments,
service-stack and all evidence are under `artifacts/evaluation/`.

Those artifacts are gitignored and are NOT available in a fresh clone. Reuse this
filesystem or explicitly preserve/copy the directory to continue elsewhere.
Private `service-stack-v1/pilot-secrets.env` is mode 0600; do not print or commit
it. oMLX key comes from `OMLX_API_KEY` in memory. Scoring uses an environment-only
`MEM0_EVAL_LLM_API_KEY`; never put keys in CLI arguments or reports. Transfer
credentials separately and securely, not with a public handoff.

Suggested model-free check command (mock socket tests may require approval):

```bash
.venv/bin/python -m pytest tests/test_benchmark_async_backend.py \
  tests/test_benchmark_service_client.py \
  artifacts/evaluation/adaptations/longmemeval-checkpoint-v1/test_cli.py \
  artifacts/evaluation/adaptations/longmemeval-checkpoint-v1/test_checkpoint.py \
  artifacts/evaluation/adaptations/longmemeval-checkpoint-v1/test_eval_client.py -q
```

Suggested new-session request: "Read scripts/evaluation/CHECKPOINT-2026-10-07.md
and resume the paused localhost benchmark goal. Verify current state and obtain
fresh approval before any live pilot inference."
