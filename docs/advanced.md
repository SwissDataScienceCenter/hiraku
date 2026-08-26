# Advanced Hiraku workflows

This guide adds experiment branches, task-branch integration, review publication, remote MLflow, and
custom programs to the [linear tutorial](tutorial.md).

## Experiment branches and lineage

Create a retained experiment branch from task-branch HEAD:

```bash
hiraku exp create heart-disease:stronger-regularization \
  --branch \
  --summary "Stronger logistic regularization" \
  --hypothesis "Stronger regularization improves validation log loss" \
  --entrypoint logistic
```

Use `--from <branch-or-commit>` to branch from another point, enabling siblings or backtracking.
Hiraku derives lineage from Git ancestry rather than storing parent IDs. `hiraku exp list
heart-disease` marks a branch with `*` when its bound commit is contained in the task branch. This is
Git containment, not the semantic acceptance decision; squash merges and cherry-picks create new
commits and therefore cannot produce the marker for the original commit.

Accept an experiment semantically in its record. Integrate it only when authorized, using normal Git:

```bash
git switch hiraku/task/heart-disease
git merge --ff-only hiraku/experiment/heart-disease/stronger-regularization
```

Hiraku does not merge, resolve conflicts, or mutate the task branch as a side effect of acceptance.
The heart-disease task still ranks `val/log_loss` first and uses `val/accuracy` as secondary evidence;
branching changes history management, not the evaluation.

## GitHub pull requests and GitLab merge requests

Configure the provider explicitly:

```yaml
review_provider: github  # or gitlab
review_remote: origin
```

First synthesize the complete review description in `task.md` and append `## Changelog`. Preview:

```bash
hiraku review create heart-disease
```

Hiraku never pushes implicitly. When publication is approved, `--push` authorizes only publication
of the task branch and creation or update of the configured review:

```bash
hiraku review create heart-disease --push
```

GitHub stores `pr_id`; GitLab stores `mr_id`. Hiraku uses `task.md` verbatim as the description,
handles provider/identity conflicts explicitly, and never approves or merges a review.

## Remote MLflow

Install the MLflow extra and configure one remote HTTP(S) URI:

```yaml
tracking_backend: mlflow
mlflow_tracking_uri: https://mlflow.example.org
```

Do not also set `mlflow_tracking_db`. Put `mlflow_experiment` in each task. Hiraku injects the same URI
into launches and uses it for run inspection. Configure authentication and TLS through standard
MLflow environment variables; never put credentials in `.hiraku.yml`. Connectivity or authentication
errors stop the operation and never trigger a local fallback. For a disposable manual test, start an
MLflow server backed by SQLite, point the URI at it, launch one experiment, and confirm `hiraku runs`
reads the same run.

Use `hiraku runs --all` only with MLflow to diagnose wrong-task, incomplete, or unassociated runs.
Commit mismatches and missing provenance remain actionable under every backend.

## Custom templates and operational boundaries

Each directory under `.hiraku/templates/` is a complete `task.md`, `program.md`, and `launch.yml`
bundle. Copy a bundled template, edit all three files, and select it with `hiraku task create <id>
--template <name>`. The repository-local Hiraku skill routes research and review work, while the
copied task `program.md` remains authoritative for approvals, permitted commands, resources, and
stopping conditions.

Programs document authority but do not enforce it. Use Git protections, credentials, execution
sandboxes, and infrastructure controls for enforcement. Protected-branch merges and all external
publication remain explicit human-authorized actions.
