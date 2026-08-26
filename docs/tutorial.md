# Linear research tutorial

This tutorial follows one strategy-neutral `heart-disease` task through two linear experiments. It
assumes a Git repository initialized with `hiraku init`. Branching and PR/MR publication belong in
the [advanced guide](advanced.md).

## Understand the artifacts

`task.md` fixes the scientific goal and evaluation and later holds the reviewed synthesis.
`program.md` defines approval and authority boundaries. `launch.yml` defines deterministic named
commands. Each experiment record connects one hypothesis and declared invocation to an exact commit;
runs are backend-managed evidence from executing that snapshot.

## Define a strategy-neutral task

```bash
hiraku task create heart-disease --template guided
git switch hiraku/task/heart-disease
```

Use this front matter in `tasks/heart-disease/task.md`:

```yaml
id: heart-disease
summary: Improve heart-disease risk prediction
goal: Minimize validation log loss on the checksum-pinned Cleveland cohort.
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
branch: hiraku/task/heart-disease
```

The body should pin the dataset and checksum, stratified split, preprocessing, seed, and evaluation.
It must not prescribe logistic regression or random forest: those are candidate experiments. Define
distinct `logistic` and `random-forest` entrypoints in `launch.yml`. Check them with:

```bash
hiraku task entrypoints heart-disease
```

Commit `task.md`, `program.md`, and `launch.yml` before binding experiments. A commit made before
`launch.yml` exists cannot reproduce that launch.

## Run the logistic baseline

```bash
hiraku exp create heart-disease:logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --entrypoint logistic
```

After the guided implementation checkpoint, implement and commit the model, then bind and launch:

```bash
hiraku exp set heart-disease:logistic-baseline --commit HEAD
hiraku launch --experiment logistic-baseline heart-disease:logistic
```

The launch must exactly match an invocation declared by the experiment. Hiraku executes a bound
commit in a temporary worktree; a working-tree launch is useful for debugging but is not reproducible
evidence.

## Compare a random forest

Create the second experiment on the same task branch and repeat the implementation, commit, bind,
and launch cycle:

```bash
hiraku exp create heart-disease:random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear feature interactions improve validation log loss" \
  --entrypoint random-forest
hiraku exp set heart-disease:random-forest --commit HEAD
hiraku launch --experiment random-forest heart-disease:random-forest
```

Inspect the compact leaderboard, select metrics explicitly when useful, and use JSON for analysis:

```bash
hiraku runs heart-disease
hiraku runs heart-disease --metrics val/log_loss,val/accuracy
hiraku runs heart-disease --sort val/log_loss --ascending --json
hiraku runs heart-disease --verbose
```

The task declaration determines default metric columns and the primary ranking direction. Without
declarations, Hiraku derives metric columns deterministically from returned runs. Human tables use
short commit hashes; records and JSON preserve exact hashes.

## Tracking choices

Table tracking is the default and writes `tasks/heart-disease/runs.csv`. Do not edit it manually.
For MLflow, install `hiraku[mlflow]`, set `tracking_backend: mlflow`, add
`mlflow_experiment: heart-disease-runs` to the task, and optionally choose a safe local
`mlflow_tracking_db`. The research commands do not change.

## Conclude and synthesize

Use `hiraku exp set` to record each experiment's status, decision, and evidence-based conclusion.
Then update `task.md` with reviewed findings across experiments, conclusions, decisions, unresolved
questions, next steps, and a concise changelog entry.

Common failures are actionable: an undeclared invocation is rejected; a run/record commit mismatch
is flagged; missing task, experiment, or commit provenance makes a run invalid; and MLflow `--all`
is reserved for investigating invalid or unassociated MLflow runs.
