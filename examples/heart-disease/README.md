# Cleveland Heart Disease example

This example compares logistic regression with a random forest on the processed Cleveland cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease). The training script downloads the official data file and requires SHA-256 checksum `a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8` before using it.

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
hiraku experiment create heart-disease:logistic \
  --summary "Logistic regression baseline" \
  --entrypoint "logistic"
hiraku experiment set heart-disease:logistic --commit HEAD
hiraku launch --experiment logistic heart-disease:logistic

hiraku experiment create heart-disease:random-forest \
  --summary "Random forest comparison" \
  --entrypoint "random-forest"
hiraku experiment set heart-disease:random-forest --commit HEAD
hiraku launch --experiment random-forest heart-disease:random-forest

hiraku runs heart-disease --sort val/log_loss --ascending
```

Both models use the same checksum-verified cohort, preprocessing rules, stratified validation split, seed, and metrics. The downloaded file is ignored by Git; provenance is established by the URL, checksum, split protocol, source commit, and tracked run configuration.

The training script selects its destination from `HIRAKU_TRACKING_BACKEND`. Table mode writes to `HIRAKU_TABLE_PATH`; MLflow mode logs the same parameters and metrics to Hiraku's injected tracking URI and experiment.

For MLflow, configure `.hiraku.yml` with `tracking_backend: mlflow` and optional `mlflow_tracking_db: mlflow.db`, and add `mlflow_experiment: heart-disease-runs` to `task.md` front matter. The launch commands do not change: the example launch wrapper adds its optional MLflow dependency when `HIRAKU_TRACKING_BACKEND=mlflow`. MLflow metrics are qualified when sorting, for example:

```bash
hiraku runs heart-disease --sort metrics.val/log_loss --ascending
```
