# Karpathy's LLM autoresearch example with Hiraku

This example adds Hiraku research records, attributed CSV runs, and
an autonomous research program to Andrej Karpathy's original
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) repository.
It does not vendor or modify the upstream training implementation.

The dispatcher currently supports Linux only. It requires GNU `timeout`,
`flock`, and the Linux UUID source at `/proc/sys/kernel/random/uuid`.

The overlay contains:

- `task.md` — the Markdown body used to create the research root.
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
hiraku example create autoresearch .
```

Keep the local Hiraku installation and run artifacts out of
Git, and commit the new files.
```bash
cat >> .gitignore <<'EOF'

# Hiraku local files and run artifacts
/.agents/
/.claude/
/hiraku/*
!/hiraku/dispatch/
/hiraku.yml
/task.md
/runs.csv
/run.log
EOF

git add .
git add hiraku/dispatch
git commit -m "add Hiraku autoresearch workflow"
```

As in the upstream
example, choose a tag and branch for the autoresearch, e.g., `sep10` and `autoresearch/sep10`. The record ID must be new.
```bash
hiraku record create sep10 --type task \
  --set 'branch=autoresearch/sep10' \
  --set "goal=Find the best architecture, optimizer, and hyperparameters to minimize `val_bpb` within the fixed 5-minute training budget." \
  --markdown-file task.md \
  --set 'program=autoresearch' \
  --set 'metrics=[{name: val_bpb, direction: minimize}]'

git switch -c autoresearch/sep10
```

## Test the CPU workflow with Hiraku

```bash
hiraku record create cpu-test --parent sep10 --type experiment \
  --set "summary=CPU-only mock run" \
  --set "hypothesis=The Hiraku dispatcher records an attributed mock result" \
  --commit HEAD

job_id=$(hiraku run --record cpu-test --backend local -- uv run python cpu_mock.py --val-bpb 1.0)
hiraku job wait "$job_id"
hiraku runs --record cpu-test

hiraku record update cpu-test \
  --set decision=discard \
  --set 'conclusion="CPU-only workflow test; not a training result."'
```

## Create the baseline experiment

Here we manually create and run the baseline experiment. Alternatively, ask your agent to invoke the Hiraku skill to create and run the baseline experiment for you.

```bash
hiraku record create baseline --parent sep10 --type experiment \
  --set "summary=Establish the baseline" \
  --set "hypothesis=Provide a reasonable starting point for comparison" \
  --commit HEAD

job_id=$(hiraku run --record baseline --backend local -- uv run python train.py)
hiraku job wait "$job_id"
hiraku runs --record baseline

hiraku record update baseline \
  --set decision=keep --set 'conclusion="Baseline run completed."'
```

## Start autoresearch

Start your favourite coding agent and a reasonably powerful model in the
initialized repository. Hiraku installs the canonical skill at
`.agents/skills/hiraku/`, discovered directly by Codex, Pi, and Gemini CLI.
Claude Code uses `.claude/skills/hiraku/`, which links to the canonical skill
when directory symlinks are available. Invoke it as `$hiraku` in Codex or
`/hiraku` in Claude Code.

Currently, Hiraku records the program association but does not automatically inject its Markdown into an agent prompt. Start an agent in the repository and ask it to
load the root record and installed program with:


```bash
Use the Hiraku skill and start the autoresearch program.

To read the root record and program, run:
hiraku record show sep10 --json
hiraku program show autoresearch
```

Track the agent's progress with:

```bash
hiraku record list --json
hiraku runs --record sep10 --all --sort val_bpb --ascending
```

### Job execution

Jobs capture committed and dirty Git-visible source in an isolated snapshot. Ignored datasets and environments are not copied; configure access through an optional dispatcher. Submission returns a job ID; wait before querying runs. Use `hiraku job logs JOB_ID` for output. Arguments after `--` replace the complete default command.
