# Karpathy's LLM autoresearch example with Hiraku

This example adds Hiraku task and experiment records, attributed CSV runs, and
an autonomous research program to Andrej Karpathy's original
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) repository.
It does not vendor or modify the upstream training implementation.

The dispatcher currently supports Linux only. It requires GNU `timeout`,
`flock`, and the Linux UUID source at `/proc/sys/kernel/random/uuid`.

The overlay contains:

- `task.md` — the Markdown body used to create the Hiraku task.
- `cpu_mock.py` — a fast CPU-only check with the same summary format as training.
- `hiraku/programs/autoresearch/program.md` — the upstream research loop adapted
  to Hiraku records and runs.
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

## Initialize the Hiraku project

From the root of the autoresearch repository, install the example. Initialization copies root `task.md` as a bootstrap template and installs the
separate program at `hiraku/programs/autoresearch/program.md`. 

```bash
hiraku init --example autoresearch
```

Keep the local Hiraku installation and run artifacts out of
Git, and commit the new files.
```bash
cat >> .gitignore <<'EOF'

# Hiraku local files and run artifacts
/.agents/
/.claude/
/hiraku/*
/hiraku.yml
/task.md
/runs.csv
/run.log
EOF

git add .
git commit -m "add Hiraku autoresearch workflow"
```

As in the upstream
example, choose a tag and branch for the autoresearch, e.g., `sep10` and `autoresearch/sep10`. The task tag must be new.
```bash
hiraku task create sep10 \
  --branch autoresearch/sep10 \
  --goal "Find the best architecture, optimizer, and hyperparameters to minimize `val_bpb` within the fixed 5-minute training budget." \
  --markdown-file task.md \
  --set 'program=autoresearch' \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={train: {command: [uv, run, python, train.py]}, mock: {command: [uv, run, python, cpu_mock.py], args: {val-bpb: 1.0}}}'

git switch autoresearch/sep10
```

## Test the CPU workflow with Hiraku

```bash
hiraku experiment create cpu-test \
  --summary "CPU-only mock run" \
  --hypothesis "The Hiraku dispatcher records an attributed mock result" \
  --commit HEAD

hiraku launch cpu-test:mock -- --val-bpb 1.0
hiraku run list

hiraku experiment update cpu-test \
  --set decision=discard \
  --set 'conclusion="CPU-only workflow test; not a training result."'
```

## Create the baseline experiment

Here we manually create and run the baseline experiment. Alternatively, ask your agent to invoke the Hiraku skill to create and run the baseline experiment for you.

```bash
hiraku experiment create baseline \
  --summary "Establish the baseline" \
  --hypothesis "Provide a reasonable starting point for comparison" \
  --commit HEAD

hiraku launch baseline:train
hiraku run list

hiraku experiment update baseline \
  --set decision=keep --set 'conclusion="Baseline run completed."'
```

## Start autoresearch

Start your favourite coding agent and a reasonably powerful model in the
initialized repository. Hiraku installs the skill for Codex at
`.agents/skills/hiraku/` and for Claude Code at `.claude/skills/hiraku/`.
Invoke it as `$hiraku` in Codex or `/hiraku` in Claude Code.

Currently, Hiraku records the program association but does not automatically inject its Markdown into an agent prompt. Start an agent in the repository and ask it to
load the canonical task and installed program with:


```bash
Use the Hiraku skill and start the autoresearch program.

To read the current task and program, run:
hiraku task show sep10 --json
hiraku program show autoresearch
```

Track the agent's progress with:

```bash
hiraku experiment list --columns id,commit,summary,hypothesis,conclusion,decision
hiraku run list --sort metric:val_bpb --ascending
```
