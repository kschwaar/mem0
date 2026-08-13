# ADR-0002: Maintain a permanent downstream product branch

- Status: Accepted
- Date: 2026-08-13
- Decision owner: Fork maintainer

## Context

This repository is a personal fork of `mem0ai/mem0`. The fork needs to absorb upstream changes regularly while retaining product-specific capabilities such as local Platform compatibility and optional Neo4j relationship-graph support.

Using `main` for both purposes would make it difficult to prove that the fork mirrors upstream, and routinely rebasing a shared product branch would rewrite published history. The desired development experience is otherwise conventional: create many short-lived feature branches, merge accepted work into a stable target, and delete the feature branches.

At the time of this decision, the local-compatibility work comprised two commits and the Neo4j work was a thirteen-commit stack on top of it. Upstream `main` had advanced by 83 commits. A rehearsal showed that integration was feasible but also demonstrated why downstream conflicts need an explicit, repeatable boundary.

## Decision

Adopt a permanent downstream product branch:

- `main` is a clean mirror of `upstream/main`.
- `personal/main` is the canonical product branch and normal integration target.
- Personal feature branches start from and merge into `personal/main`.
- Upstream updates fast-forward into `main`, after which `main` is merged into `personal/main`.
- Published `personal/main` history is not routinely rebased or force-pushed.
- Branches intended for submission to the original OSS project start from `main` and remain isolated from downstream-only work.

The executable agent contract, including stop conditions and prohibited operations, lives in the root `AGENTS.md` under "Downstream Branch Governance (Binding)."

## Rationale

This model retains the maintainer's familiar short-lived feature workflow while assigning upstream mirroring and product integration to different permanent branches. Upstream conflicts are resolved against an already integrated product state, merge commits preserve feature and synchronization boundaries, and deployments or collaborators can rely on stable commit identities.

## Consequences

### Positive

- `main` can be compared directly with `upstream/main`.
- `personal/main` is stable, deployable, and does not require routine force-pushes.
- Feature branches remain short-lived and can use ordinary PR review and deletion.
- Upstream synchronization decisions and conflict resolutions remain visible in history.
- Independent features can be developed concurrently from a common product baseline.

### Negative

- `personal/main` contains merge commits and will not be a minimal linear patch series.
- Fork-specific governance files exist only on the product line because adding them to `main` would violate the mirror contract.
- Contributors and automation must use `personal/main` as the default personal PR base.
- Sending a downstream feature upstream may require recreating or cherry-picking it onto a clean branch based on `main`.

## Alternatives considered

### 1. Rebase-driven personal patch stack

Regularly rebase `personal/main` onto `main` and force-push the rewritten result.

This produces a clean `main..personal/main` patch series and can work when the fork contains only a few small, carefully curated patches. It was rejected because merged independent features, deployments, open branches, and collaborators benefit from stable commit identities. Conflict resolution can also repeat as each downstream commit is replayed.

Reconsider this option if the downstream delta shrinks to a small patch queue, no system depends on stable `personal/main` commit IDs, and upstream submission or patch portability becomes more important than ordinary product-branch history.

### 2. Disposable integration branch assembled from persistent feature branches

Keep each downstream capability on a long-lived branch, rebase those branches independently, and recreate an integration branch from current `main` whenever upstream changes.

This makes capabilities independently selectable and is useful for maintaining several product variants. It was rejected because feature branches cease to be short-lived, dependency ordering becomes operational configuration, and integration resolutions may need to be recreated.

Reconsider this option if the fork must ship multiple combinations of features, capabilities need independent release lifecycles, or removing one capability from the product must be routine.

### 3. Squashed downstream feature commits

Keep the permanent `personal/main` branch but squash every feature into one commit before integration.

This retains a compact downstream history and makes whole-feature reverts easy. It was not selected as a mandatory policy because large features benefit from reviewable internal history and a single commit can become an unwieldy conflict unit. Squash merge remains permitted as a feature-level choice.

Reconsider making squash mandatory if downstream history becomes noisy, feature-level reverts are common, and the detailed history remains reliably available in PRs.

### 4. Personal commits directly on `main`

Use the fork's `main` as the product branch and merge upstream into it.

This is the simplest single-branch workflow but destroys the clean-mirror invariant and makes upstream state harder to audit. It is rejected unless maintaining a clean mirror no longer has value.

## Migration and rollback

Switching strategies requires an explicit replacement ADR. Before rewriting or replacing any published branch, create backup refs, record the old and new base commits, account for open PRs and deployments, and obtain maintainer approval for any force-push. Do not silently evolve from this model through ad hoc rebases.
