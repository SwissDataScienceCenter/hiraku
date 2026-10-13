# Karpathy's LLM autoresearch example with Hiraku

This example adds Hiraku research records, attributed CSV runs, and
Hiraku's autoresearch workflows to Andrej Karpathy's original
[`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) repository.
It does not vendor or modify the upstream training implementation.

The dispatcher currently supports Linux only. It requires GNU `timeout`,
`flock`, and the Linux UUID source at `/proc/sys/kernel/random/uuid`.

The overlay contains:

- `task.md` — the Markdown body used to create the research root.
- `cpu_mock.py` — a fast CPU-only check with the same summary format as training.
- `hiraku/workflows/autoresearch/workflow.md` — the upstream research loop
  adapted to Hiraku records and runs, as a Markdown-only workflow.
- `hiraku/dispatch/autoresearch.sh` — timeout, output parsing, and CSV tracking.
  It records an explicit run `status` (`finished`, `crashed`, or `timeout`),
  which the `autoresearch-python` workflow uses as success evidence.

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
autoresearch workflows at `hiraku/workflows/autoresearch/` and
`hiraku/workflows/autoresearch-python/`.

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
installs its skill for Codex at `.agents/skills/hiraku/` and for Claude Code at
`.claude/skills/hiraku/`.

Ask the agent to start the workflow, for example:

```text
Start Hiraku autoresearch-python on task sep10 with the train entrypoint for
ten experiments, using the baseline run as reference.
```

The agent calls the `autoresearch-python` tool and follows the upstream loop step by
step without asking you: for each experiment it commits one idea, creates an
experiment record bound to the commit, launches it with `hiraku launch`, and
submits the tracked run. Hiraku checks the run's attribution, its `status`, and
its `val_bpb`, decides keep, discard, or crash against the retained result, and
stores the assessment on the record. The agent records the decision and, for
discard or crash, resets the branch to the retained commit. Unlike the
upstream loop, the session ends after the requested number of experiments.

To decide each step yourself, start `guided-research` instead: you choose among
proposed hypotheses, approve each launch, and discuss each result.

To run the upstream procedure instead, start `autoresearch`. It sends the
whole loop as one instruction: the agent experiments, advances or resets the
branch, and continues until you stop it, then reports a summary. Hiraku does
not check budgets or evidence in this mode. To continue an interrupted session,
ask the agent to resume it; it uses `session_list` and `session_show`. Inspect
sessions from a terminal with:

```bash
hiraku session list
hiraku session show <session-id>
```

Track the agent's progress with:

```bash
hiraku record list --json
hiraku runs --record sep10 --all --sort val_bpb --ascending
```
