# Evaluation collaboration board

The [active goal](active-goal.md) is the current scope and status.
This board preserves historical ownership and dependencies; terminal references
and next-step claims below describe their dated sessions.
The [integration plan](benchmark-integration.md) retains the earlier plan.
The [workbench guide](README.md) lists existing runnable tools. Research reports
are supporting evidence. This board records ownership, dependencies and status.

## Planning review completed on 2026-10-06

Two read-only Codex CLI reviewers ran in cmux workspace `workspace:11`, named
`Mem0 benchmark planning`. API review used `surface:38`; dataset review used
`surface:39`. Both completed and their findings were reconciled into the plan.
These IDs belong to this cmux instance and are not portable launch commands.

Their captured reports are `/tmp/mem0-benchmark-api-review.md` and
`/tmp/mem0-benchmark-data-review.md`. The initial shared coordination log is
`/tmp/mem0-benchmark-collaboration.md`. Temporary files are optional supporting
artifacts; the reviewed decisions and work breakdown live in this repository.
No datasets, service builds, benchmark ingestion or model calls were performed.

## Current coordinator checkpoint — 2026-10-07

This checkpoint supersedes the historical readiness/next-step statements below.
The main quality goal is active, not achieved. Model-discovery workspace:14,
surface:46, holds the interactive candidate investigation at
`/private/tmp/mem0-model-discovery.ffljKn` and redacted direct-model reports.
No autonomous parallel inference is permitted.

The live memory preview established dynamic binding. Quitting Docker improved
headroom but left the 27B model unsuitable for everyday coexistence. Qwen3 14B
passed three of four direct cases, failing third-party exclusion. Gemma passed
four of four; Ministral stopped on its first missing-schema response. All are
diagnostic findings, not population-quality estimates.

Coordinator completed a real, fresh-storage Gemma SDK trajectory: nine
operations, three adds, eleven passing phrase checks; actual correction UPDATE,
scope separation and persistence after reload. See
[result and limitations](gemma-trajectory-first.md). OpenAI-compatible Python
configuration now supports opt-in backend JSON fields and client timeout/retry
controls, with regressions and public documentation. No service default,
persistent oMLX settings or personal memories changed.

Next owned work: coordinator verifies normal-workload capacity and linked Python
traces, then fresh mutation fixtures, exact-fork wrapper/checkpoint integration,
and revision-pinned dataset inventory. Model-free preparation can proceed while
desktop/Docker restoration waits on the user. Full benchmark remains gated.

Follow-on preparation: Docker socket is absent, so live Langfuse/coexistence
verification is not yet available. Coordinator added a model-free tested
whole-run trajectory watchdog with default three-add/nine-event/300-second
envelope, no experiment retries, exclusive artifacts, and partial-ledger
reconciliation. This bounds the evaluation process, not server cancellation,
resource pressure, hidden call fan-out or product acceptance. User has been asked
to restore Docker/normal applications; no automatic service startup occurred.

## Historical scoped research and gates (superseded by checkpoint above)

### Hardware-aware readiness pass

The user confirmed oMLX at `http://127.0.0.1:8080/v1` and selected
`Qwen3.8-27B-4bit`. See [oMLX readiness](omlx-readiness.md) for authenticated
metadata, host/container resources and the exact current inference gate.

Hardware reviewer `surface:41` and dataset-pilot reviewer `surface:42` completed
read-only research in workspace:11. Their read-only CLI sessions could not write
the requested final `/tmp` reports; the coordinator collected terminal findings
and independently verified hardware and oMLX metadata through authorized host
diagnostics. Treat the board and readiness report as the retained findings.
Dataset row inventory and actual selected pilot IDs remain pending.

The coordinator implemented pure benchmark request/result translation helpers
and regression tests. All 41 focused Python tests pass (17 new compatibility
cases plus 24 existing harness/trajectory tests), with Ruff checks passing.
These helpers are not yet connected to the HTTP wrapper. One direct endpoint
embedding smoke completed with 1024 finite dimensions in approximately 1.23
seconds and no observed swap growth. No extraction/judge calls, dataset downloads,
new services or memory-limit changes have occurred. The requested extraction
model remains gated by its resident estimate exceeding oMLX's dynamic ceiling.

Current next steps: connect/test the isolated wrapper and harden checkpoints;
inventory pinned dataset bytes without inference; resolve the selected model's
memory admission gate, then one embedding/extraction pair, tiny trajectory and
one complete eligible history. Concurrency remains one throughout this ramp.

### Separate oMLX investigation

oMLX memory-policy diagnosis now lives in cmux **oMLX memory investigation**,
`workspace:12`, rooted at `/tmp/omlx-memory-investigation`. Its scoped agent
investigates the binding static/dynamic/Metal constraint and resource-respecting
options. It is authorized for diagnosis only, with no settings, kernel-limit,
service lifecycle or inference changes. See that workspace's README and eventual
`findings.md`; the coordinator imports only the readiness result into Mem0 work.
The observed ceiling is a process-wide derived oMLX limit, not a per-model setting.
The configured tier is balanced; which component binds at a given moment still
requires the breakdown. Mem0 extraction remains dependent on this investigation.

### Separate exact-model comparison

Primary-source web research now runs in cmux **Qwen model comparison**,
`workspace:13`, rooted at `/tmp/omlx-model-comparison`. It compares the exact
`lmstudio-community/Qwen3-14B-MLX-4bit` and
`mlx-community/Qwen3.8-27B-4bit` releases, including quantization, architecture,
thinking/structured-output behavior, memory implications and a controlled Mem0
comparison design. No inference or settings changes are part of that scope.
The user-selected 27B model remains the intended starting extraction model.

Current authenticated oMLX metadata confirms the 14B model is unloaded and not
loading. Its resident estimate (~8.12 GiB) does not set the global ceiling.
Only the embedding model is loaded. The latest final-ceiling snapshot is
10,585,572,744 bytes (~9.86 GiB), demonstrating that the value changes separately
from the fixed model-size estimates.

| Goal                                                             | Owner                           | Dependencies                                 | Deliverable                                                            | Status  |
| ---------------------------------------------------------------- | ------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------- | ------- |
| Exact-fork compatibility adapter and failure/checkpoint handling | Unassigned implementation agent | Pinned fork and suite                        | Adapter, manifest guard and contract tests                             | Planned |
| Dataset inventory and grouped split                              | Unassigned data agent           | Dataset revision/license selection           | Download manifest, selected IDs, ingestion estimate and leakage report | Planned |
| Python runtime tracing and Langfuse readback                     | Unassigned observability agent  | Compatibility adapter                        | Linked span/score smoke and local ledger                               | Planned |
| Local baseline and reviewed component cases                      | Coordinator                     | First three goals                            | Baseline and trace-derived cases with reviewed labels                  | Planned |
| Actual service acceptance and model comparison                   | Coordinator                     | Baseline, quality targets and held-out cases | Per-category acceptance report                                         | Planned |

## Collaboration contract

The coordinator owns integration decisions and this board. Research agents
publish findings with sources, limitations and concrete completion evidence.
Implementation agents use independent branches/worktrees based on current
`personal/main`; do not create concurrent editors in this shared checkout.
If work depends on the existing `feature/ai-call-evaluation` implementation,
integrate that base feature first under the repository's branch governance.
Only the coordinator merges accepted work after affected checks pass.

Keep each agent's scope, owned files, dependencies, acceptance checks, output
location and live session handle on the board. cmux supplies terminal visibility;
the board and artifacts supply shared state. A terminal pane alone does not
prove an agent is still running. Record blocked/finished status explicitly and
reconcile outputs before the next dependent goal begins.

No inference runs compete for the same localhost model resources during quality
comparisons. Dataset preparation and contract tests may proceed independently.
Keep raw data, credentials and generated benchmark results out of git; retain
redacted manifests, hashes and source IDs with the experiment artifacts.
