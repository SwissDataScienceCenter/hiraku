# Digits classification with MLflow

Train a small neural network on the bundled Digits dataset and compare two L2
regularization strengths. The script logs per-epoch loss and accuracy to
MLflow; Hiraku queries final metrics and record attribution.

```bash
mkdir digits-mlflow && cd digits-mlflow
git init
hiraku init --example digits-mlflow
uv sync
git add . && git commit -m "initialize Digits example"
```

```bash
hiraku record create digits --type task \
  --set 'goal=Compare Digits regularization curves' \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}'

hiraku record create weak-l2 --parent digits --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {alpha: 0.0001, epochs: 40, hidden-units: 64, seed: 42, validation-fraction: 0.25}}}'

hiraku record create strong-l2 --parent digits --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {alpha: 1.0, epochs: 40, hidden-units: 64, seed: 42, validation-fraction: 0.25}}}'

hiraku launch train --record weak-l2
hiraku launch train --record strong-l2
hiraku runs --record digits --descendants --sort val/log_loss --json
```

MLflow stores both runs under `hiraku-digits`, with `hiraku.record_id` tags
identifying their Hiraku records. Set `MLFLOW_TRACKING_URI` and the record's
`tracking_backend` consistently if using a remote server. Inspect complete
metric histories in the MLflow UI.
