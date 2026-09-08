# Hiraku autoresearch example

This standalone example adapts Karpathy's GPU autoresearch loop to the current
Hiraku workflow. The exact upstream revision, copied files, and license are
recorded in [UPSTREAM.md](UPSTREAM.md). An upstream run tag maps to a Hiraku
task, each candidate commit maps to an experiment, and each result maps to a
Hiraku run.

The vendored `prepare.py` and `train.py` remain pinned upstream files.
`run_gpu.py` adds Hiraku tracking around the GPU training process, while
`cpu_mock.py` validates the workflow without a GPU.

## Initialize

Copy this directory outside the Hiraku checkout, then run:

```bash
uv sync
git init -b main
hiraku init
git add .
git commit -m "initialize autoresearch repository"

hiraku task create autoresearch \
  --goal "Minimize val_bpb under a fixed five-minute single-GPU budget" \
  --question "Which model and optimizer changes improve validation bits per byte?" \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={gpu: {command: [uv, run, python, run_gpu.py]}, cpu-mock: {command: [uv, run, python, cpu_mock.py], args: {val-bpb: 1.0}}}'
```

Read [program.md](program.md), choose one history mode, and record the choice
in the task record with `hiraku task edit autoresearch`.

Run `uv run prepare.py` once to download data and train the tokenizer. Real
experiments require one NVIDIA GPU, CUDA-compatible PyTorch, roughly five
minutes per run, and enough storage for the prepared data. The wrapper enforces
a ten-minute hard timeout.

## Establish a baseline

```bash
hiraku experiment create baseline \
  --summary "Unmodified upstream baseline" \
  --hypothesis "The pinned upstream implementation establishes the reference val_bpb" \
  --commit HEAD \
  --branch

hiraku launch --experiment baseline gpu > run.log 2>&1
hiraku run list --task autoresearch --sort val_bpb --ascending
```

The copied upstream `train.py` is the experiment surface; keep `prepare.py`,
its evaluation function, dependencies, and the time budget fixed.

## CPU contract check

The CPU mock exercises launch attribution, commit-isolated execution, tracking,
and ranking without making claims about GPU training or model quality:

```bash
hiraku experiment create contract \
  --summary "CPU-only workflow contract" \
  --hypothesis "The Hiraku workflow records an attributed mock result" \
  --commit HEAD

hiraku launch --experiment contract cpu-mock --val-bpb 1.0
hiraku run list --task autoresearch --experiment contract --json
```

CSV tracking is used by default and writes
`hiraku/tasks/autoresearch/runs.csv`. Do not edit that file manually.
