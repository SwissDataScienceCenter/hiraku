# Hiraku autoresearch example

This standalone template adapts Karpathy's GPU autoresearch loop to Hiraku. The exact upstream revision, copied files, and license are recorded in [`UPSTREAM.md`](UPSTREAM.md). It uses table tracking by default; the same wrapper supports Hiraku's local MLflow backend.

## Initialize

Copy this directory outside the Hiraku checkout, then:

```bash
git init -b main
hiraku init
hiraku task create autoresearch --template autoresearch
git switch hiraku/task/autoresearch
git add .
git commit -m "initialize autoresearch task"
```

Run `uv sync`, then `uv run prepare.py` once to download data and train the tokenizer. Real experiments require one NVIDIA GPU (upstream was tested on H100), CUDA-compatible PyTorch, roughly five minutes of training per run, and enough storage for prepared data. The program enforces a ten-minute hard timeout and the task fixes `val_bpb` as the deciding metric.

Create and launch a baseline:

```bash
hiraku exp create autoresearch:baseline --branch --entrypoint gpu
git add train.py tasks/autoresearch/experiments/baseline.yml
git commit -m "define autoresearch baseline"
hiraku exp set autoresearch:baseline --commit HEAD
hiraku launch --experiment baseline autoresearch:gpu
hiraku runs autoresearch --sort val_bpb --ascending
```

The copied upstream `train.py` remains the experiment surface; `prepare.py` and its evaluation are fixed. `run_gpu.py` translates the upstream summary into the configured Hiraku tracking backend.

## CPU contract check

The CPU mock exercises branch, launch, provenance, tracking, and ranking behavior without claiming to validate GPU training or model quality:

```bash
hiraku exp create autoresearch:contract --entrypoint "cpu-mock --val-bpb 1.0"
hiraku launch --experiment contract autoresearch:cpu-mock --val-bpb 1.0
hiraku runs autoresearch --sort val_bpb --ascending
```

For MLflow, set `tracking_backend: mlflow` (and optionally `mlflow_tracking_db`) in `.hiraku.yml`, add `mlflow_experiment: autoresearch` to `task.md`, and install with `uv sync --extra mlflow`. Hiraku supplies the local SQLite tracking URI to the wrapper.
