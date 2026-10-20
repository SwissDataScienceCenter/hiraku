# Karpathy's LLM autoresearch example with Hiraku

This example adds Hiraku research records, attributed CSV runs, and
an autonomous research workflow to Andrej Karpathy's original
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) repository.
It does not vendor or modify the upstream training implementation.

The dispatcher currently supports Linux only. It requires GNU `timeout`,
`flock`, and the Linux UUID source at `/proc/sys/kernel/random/uuid`.

The overlay contains:

- `task.md` — the Markdown body used to create the research root.
- `cpu_mock.py` — a fast CPU-only check with the same summary format as training.
- `hiraku/workflows/autoresearch/workflow.md` — the upstream research loop
  adapted to Hiraku records and runs. A small `workflow.py` sidecar sends the
  whole body as one final instruction.
- `hiraku/dispatch/autoresearch.sh` — timeout, output parsing, and CSV tracking.
  It records an explicit run `status` (`finished`, `crashed`, or `timeout`).

`hiraku init` also installs the bundled workflows under `hiraku/workflows/` and
configures the Hiraku MCP server for Claude Code (`.mcp.json`) and Codex
(`.codex/config.toml`). Each workflow, including the example's `autoresearch`,
is available as an MCP tool and as a prompt named after it.

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
autoresearch workflow at `hiraku/workflows/autoresearch/`.

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
/.codex/
/.mcp.json
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
example, choose a tag and branch for the autoresearch, e.g., `sep10` and `autoresearch/sep10`. The record ID must be new.
```bash
hiraku record create sep10 --type task \
  --set 'branch=autoresearch/sep10' \
  --set "goal=Find the best architecture, optimizer, and hyperparameters to minimize `val_bpb` within the fixed 5-minute training budget." \
  --markdown-file task.md \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={train: {command: [uv, run, python, train.py]}, mock: {command: [uv, run, python, cpu_mock.py], args: {val-bpb: 1.0}}}'

git switch -c autoresearch/sep10
```

## Test the CPU workflow with Hiraku

```bash
hiraku record create cpu-test --parent sep10 --type experiment \
  --set "summary=CPU-only mock run" \
  --set "hypothesis=The Hiraku dispatcher records an attributed mock result" \
  --commit HEAD

hiraku launch mock --record cpu-test -- --val-bpb 1.0
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

hiraku launch train --record baseline
hiraku runs --record baseline

hiraku record update baseline \
  --set decision=keep --set 'conclusion="Baseline run completed."'
```

## Start autoresearch

Start your favourite coding agent with a reasonably powerful model in the
initialized repository. It connects to the Hiraku MCP server configured by
`hiraku init`; the `hiraku` command must be on the agent's `PATH`. Hiraku also
installs the canonical skill at `.agents/skills/hiraku/`, discovered directly
by Codex, Pi, and Gemini CLI. Claude Code uses `.claude/skills/hiraku/`, which
links to the canonical skill when directory symlinks are available. Invoke it
as `$hiraku` in Codex or `/hiraku` in Claude Code.

Start the workflow with its prompt, `/mcp__hiraku__autoresearch` in Claude
Code, or ask the agent to start it, for example "Start Hiraku autoresearch on
task sep10". It sends the upstream loop as one instruction: the agent
experiments, advances or resets the branch, and continues until you stop it,
then reports a summary. Hiraku does not check budgets or evidence in this mode.

To decide each step yourself, start the bundled `collaborative` workflow
instead: the agent drafts, implements, launches, and analyzes one experiment at
a time, and you review each step before choosing the next one.

To continue an interrupted session, ask the agent to resume it; it uses
`session_list` and `session_show`. Inspect sessions from a terminal with:

```bash
hiraku session list
hiraku session show <session-id>
```

Track the agent's progress with:

```bash
hiraku record list --json
hiraku runs --record sep10 --all --sort val_bpb --ascending
```
