# Cleveland Heart Disease example

This example provides a guided comparison and a bounded autonomous flow on the processed Cleveland
cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease).
The training script downloads the official data file and verifies SHA-256 checksum
`a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8` before using it.

Copy this directory outside the Hiraku checkout, then initialize it:

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
hiraku task create heart-disease --template heart-disease
git switch hiraku/task/heart-disease
git add .
git commit -m "create Heart Disease task"
```

Create and run the two experiments:

```bash
hiraku experiment create heart-disease:logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --entrypoint "logistic"
hiraku experiment set heart-disease:logistic-baseline --commit HEAD
hiraku launch --experiment logistic-baseline heart-disease:logistic

hiraku experiment create heart-disease:random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear feature interactions improve validation log loss" \
  --entrypoint "random-forest"
hiraku experiment set heart-disease:random-forest --commit HEAD
hiraku launch --experiment random-forest heart-disease:random-forest

hiraku runs heart-disease --sort val/log_loss --ascending
```

With the dashboard extra installed, review the same evidence interactively:

```bash
hiraku dashboard heart-disease
```

Switch between `val/log_loss` and `val/accuracy`, compare the two experiments, and inspect the
direction-aware frontier. The dashboard is read-only; use the CLI or Hiraku skill to update records.

Both models use the same checksum-verified cohort, preprocessing rules, stratified validation split, seed, and metrics. The downloaded file is ignored by Git; provenance is established by the URL, checksum, split protocol, source commit, and tracked run configuration.

For a bounded autonomous task, create the second complete template instead:

```bash
hiraku task create heart-disease-auto --template heart-disease-auto
git switch hiraku/task/heart-disease-auto
```

Fill and approve the iteration or wall-clock limit and Git permissions in its `program.md` before
letting the loop start. It uses the same fixed data and evaluation protocol, permits CPU-only edits
to training and model configuration with existing dependencies, and ends with a human-reviewed task
synthesis.

The training script selects its destination from `HIRAKU_TRACKING_BACKEND`. Table mode writes to `HIRAKU_TABLE_PATH`; MLflow mode logs the same parameters and metrics to Hiraku's injected tracking URI and experiment.

For MLflow, configure `.hiraku.yml` with `tracking_backend: mlflow` and either optional local
`mlflow_tracking_db: mlflow.db` or remote `mlflow_tracking_uri: https://mlflow.example.org`, and add
`mlflow_experiment: heart-disease-runs` to `task.md` front matter. The two locations are mutually
exclusive; remote authentication uses standard MLflow environment variables. The launch commands do
not change. MLflow metrics are qualified when sorting, for example:

```bash
hiraku runs heart-disease --sort metrics.val/log_loss --ascending
```
