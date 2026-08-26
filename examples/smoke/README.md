# Hiraku smoke example

Copy this directory outside the Hiraku checkout, then run:

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize smoke example"

hiraku task create smoke --template smoke
git switch hiraku/task/smoke
git add .
git commit -m "create smoke task"

hiraku experiment create smoke:baseline \
  --summary "Run the deterministic baseline" \
  --entrypoint "train --score 0.50"
hiraku experiment set smoke:baseline --commit HEAD
hiraku launch --experiment baseline smoke:train --score 0.50
hiraku runs smoke --sort score --descending
hiraku experiment set smoke:baseline \
  --status evaluated \
  --decision accepted \
  --conclusion "The deterministic baseline completed."
```

The launcher executes the committed source from a detached temporary worktree. `train.py` reads `HIRAKU_TRACKING_BACKEND`: table mode writes to the injected `HIRAKU_TABLE_PATH`, while MLflow mode uses the injected Hiraku MLflow URI and experiment name.

To run the same example with MLflow, set the project configuration to:

```yaml
tracking_backend: mlflow
mlflow_tracking_db: mlflow.db  # optional; this is the default
```

Then add `mlflow_experiment: smoke-runs` to the task's `task.md` front matter. The experiment and launch commands above remain unchanged: the example launch wrapper adds its optional MLflow dependency when `HIRAKU_TRACKING_BACKEND=mlflow`. Inspect the MLflow metric with `hiraku runs smoke --sort metrics.score --descending`; Hiraku determines the project-root tracking location and injects it into the launched process.
