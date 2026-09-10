# Hiraku autoresearch overlay

This example adds Hiraku task and experiment records, attributed CSV runs, and
an autonomous research program to Andrej Karpathy's original
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) repository.
It does not vendor or modify the upstream training implementation.

The overlay contains:

- `program.md` — the upstream research loop adapted to Hiraku records and runs.
- `task.md` — the Markdown body used to create the Hiraku task.
- `cpu_mock.py` — a fast contract check with the same summary format as training.
- `hiraku/dispatch/autoresearch.sh` — timeout, output parsing, and CSV tracking.

## Setup

First create the original repository checkout and change to its root, following the upstream setup instructions:
```bash
git clone https://github.com/karpathy/autoresearch.git
cd autoresearch

uv sync

uv run prepare.py

# verify that the training script runs without error
# requires a CUDA-compatible GPU. With 16 GB VRAM, edit train.py and set `DEVICE_BATCH_SIZE = 32 `
uv run train.py
```

## Hiraku overlay

From the root of the initialized upstream checkout, point `example_dir` at this
example in a local Hiraku source checkout:

```bash
example_dir=/path/to/hiraku/docs/examples/autoresearch

hiraku init
cp "$example_dir"/{program.md,task.md,cpu_mock.py} .
install -m 755 "$example_dir/hiraku/dispatch/autoresearch.sh" \
  hiraku/dispatch/autoresearch.sh
```

In the generated `hiraku.yml`, set:

```yaml
default_dispatch: autoresearch.sh
```

Commit the initialized Hiraku project and overlay before creating the task:

```bash
git add hiraku.yml hiraku .agents program.md task.md cpu_mock.py
git commit -m "add Hiraku autoresearch workflow"

hiraku task create autoresearch \
  --goal "Minimize val_bpb under a fixed five-minute single-GPU budget" \
  --question "Which model and optimizer changes improve validation bits per byte?" \
  --markdown-file task.md \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={gpu: {command: [uv, run, python, train.py]}, cpu-mock: {command: [uv, run, python, cpu_mock.py], args: {val-bpb: 1.0}}}'

git switch hiraku/tasks/autoresearch
git add hiraku.yml hiraku/tasks/autoresearch/task.md
git commit -m "create autoresearch task"
```

No ignore rules are added in this first version. Hiraku experiment records,
`runs.csv`, and `run.log` will therefore appear in Git status unless you manage
them locally. The dispatcher records the actual clean or dirty state.

## Record the baseline with Hiraku


```bash
hiraku experiment create contract \
  --summary "CPU-only workflow contract" \
  --hypothesis "The Hiraku dispatcher records an attributed mock result" \
  --commit HEAD

hiraku launch contract:cpu-mock -- --val-bpb 1.0
hiraku run list --task autoresearch --experiment contract --json
```

## Start autoresearch

Read `task.md` and `program.md`, then follow the setup in the program. The first
GPU run establishes the unmodified upstream baseline:

```bash
hiraku experiment create baseline \
  --summary "Unmodified upstream baseline" \
  --hypothesis "The upstream implementation establishes the reference val_bpb" \
  --commit HEAD

hiraku launch baseline:gpu > run.log 2>&1
hiraku run list --task autoresearch --sort val_bpb --ascending
```

The dispatcher writes execution status and parsed metrics to project-root
`runs.csv`. Record the research decision to keep, discard, or abandon an
experiment, together with the supporting evidence, in its experiment record.
