# Environment and reproducibility

The minimum useful provenance is attached to every Run:

| Fact | Current field |
|---|---|
| exact source | `commit_sha` |
| entrypoint | `entrypoint` |
| execution lifecycle | `started_at`, `finished_at`, `status`, `exit_code` |
| Python | provenance `python`, `python_executable` |
| dependency identity | provenance `lockfile` SHA-256 descriptor when a lockfile exists |
| platform | provenance `platform`, `machine` |
| accelerator hint | provenance `accelerator` from `CUDA_VISIBLE_DEVICES` |
| container hint | provenance `container` when running under `/.dockerenv` |

The launcher injects `HIRAKU_RUN_ID`, `HIRAKU_GOAL_ID`, `HIRAKU_EXPERIMENT_ID`,
`HIRAKU_EXPERIMENT_KIND`, `HIRAKU_GIT_COMMIT`, `HIRAKU_METRICS_PATH`, `HIRAKU_PROJECT_ROOT`, and
`HIRAKU_STATE_PATH`. A child `hiraku run log` therefore writes to the branch-independent database,
including when launch uses a detached temporary worktree.

This PoC does not claim bit-exact environment recreation. The lockfile digest helps identify whether
two Runs used the same dependency input; containers and external environments remain user-managed.

## Worktrees

Durable per-Experiment worktrees were valuable in the earlier prototype for concurrent conflicting environments, but
they are deliberately deferred here. Experiment creation uses one normal Git branch and checks it
out automatically. `hiraku launch --commit` creates and removes a temporary detached worktree only
when reproducible execution must be isolated from the active checkout.
