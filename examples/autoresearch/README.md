# Hiraku autoresearch example

This standalone template adapts Karpathy's GPU autoresearch loop to Hiraku while keeping the pinned upstream `program.md` structure and wording as closely as possible. The exact upstream revision, copied files, and license are recorded in [`UPSTREAM.md`](UPSTREAM.md). An upstream run tag maps to a Hiraku task, each candidate commit maps to an experiment record bound to its implementation commit, and upstream result rows map to Hiraku runs. It uses table tracking by default; the same wrapper supports Hiraku's local MLflow backend.

## Initialize

Copy this directory outside the Hiraku checkout, then:

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize autoresearch repository"

# Choose a fresh run tag/task, for example mar5.
hiraku task create mar5 --template autoresearch
# Edit tasks/mar5/task.md and record the selected history mode.
git switch hiraku/task/mar5
git add tasks/mar5
git commit -m "initialize autoresearch task mar5"
```

Run `uv sync`, then `uv run prepare.py` once to download data and train the tokenizer. Real experiments require one NVIDIA GPU (upstream was tested on H100), CUDA-compatible PyTorch, roughly five minutes of training per run, and enough storage for prepared data. The program enforces a ten-minute hard timeout and the task fixes `val_bpb` as the deciding metric.

Before starting, choose one history mode and record it in `task.md`:

- `revert`: experiments run on the task branch; rejected implementation commits are undone with `git revert`.
- `reset`: experiments run on the task branch; rejected implementation commits are discarded with `git reset --hard`, matching upstream.
- `branch`: every experiment gets `hiraku/experiment/<task>/<experiment>`; accepted branches are merged into the task branch, rejected branches remain unmerged, and all experiment branches are retained.

Create and launch a baseline:

```bash
hiraku exp create mar5:baseline --branch --entrypoint gpu
hiraku exp set mar5:baseline --commit HEAD --status active
hiraku launch --experiment baseline mar5:gpu > run.log 2>&1
hiraku runs mar5
hiraku exp set mar5:baseline --status evaluated --decision accepted \
  --conclusion "Established the unchanged baseline."
git switch hiraku/task/mar5
git merge --ff-only hiraku/experiment/mar5/baseline
```

The commands above illustrate `branch` mode; `program.md` defines all three loops. The copied upstream `train.py` remains the experiment surface; `prepare.py` and its evaluation are fixed. `run_gpu.py` translates the upstream summary into the configured Hiraku tracking backend. Do not create an additional `results.tsv`: with table tracking, Hiraku writes `tasks/<task>/runs.csv` automatically and leaves it uncommitted.

## CPU contract check

The CPU mock exercises branch, launch, provenance, tracking, and ranking behavior without claiming to validate GPU training or model quality:

```bash
hiraku exp create mar5:contract --entrypoint "cpu-mock --val-bpb 1.0"
hiraku launch --experiment contract mar5:cpu-mock --val-bpb 1.0
hiraku runs mar5
```

For MLflow, set `tracking_backend: mlflow` (and optionally `mlflow_tracking_db`) in `.hiraku.yml`, add a task-specific `mlflow_experiment` to `task.md`, and install with `uv sync --extra mlflow`. Hiraku supplies the local SQLite tracking URI to the wrapper.
