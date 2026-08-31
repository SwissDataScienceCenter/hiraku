---
project: <project name>
active_goals: []
bounds:
  max_runs_per_goal: 40
  max_tree_depth: 4
  max_concurrent_worktrees: 3
  max_wallclock_hours_per_run: 6
promotion:
  reproduction_seeds: 1
  agreement_tolerance: 0.01
records:
  history: false            # true = keep a `## History` section in goal records
agent:
  command:                  # empty = HIRAKU_AGENT_CMD, then `claude -p`
  unset_env: []             # e.g. [ANTHROPIC_API_KEY] to use the claude.ai login instead
  timeout: 900
tracking:
  backend: table            # table | table+mlflow
  mlflow:
    tracking_uri:           # e.g. http://mlflow.example.org:5000; empty = ./mlruns
    experiment:             # empty = use the goal id as the MLflow experiment name
context:
  sources:
    - CLAUDE.md
    - AGENTS.md
    - README.md
    - docs/**/*.md
    - notes/**/*.md
    - research/config.md
    # Paths may reach outside this project — a research project usually studies a
    # separate code repository. Absolute paths and ../ both work, for example:
    #   - ../mapillary_alignment/CLAUDE.md
    #   - ../mapillary_alignment/docs/**/*.md
  max_files: 40
  max_bytes_per_file: 60000     # a bigger file is cut on a heading boundary; the excerpt
  max_total_bytes: 400000       # names the sections it left out. `hiraku-auto context` shows it.
---

# Research configuration

The project-level contract. A node's `policy.md` may narrow anything here; it may never widen it.

## Goals

List the active goals and one line each on what they are for. Goal records live in
`research/goals/`.

## Environment

How the environment is built, and what a run may assume.

- Build: e.g. `uv sync`
- Python: e.g. `>=3.14,<3.15`
- Hardware assumed: what a run may expect to find
- `accelerator` string convention: the exact format runs write into the run table, so rows are
  comparable
- Determinism: whether runs are expected bit-reproducible at a fixed seed, and if not, the sources
  of variance

## Records

`records.history` is off. Git already holds the history of every record — who changed what, when,
and the whole text before and after — and a `## History` section only duplicates that badly: the
draft tool appends to it on every `--revise`, so it fills with near-identical entries nobody reads.
Set it to `true`, or pass `--history` to a single `goal add` / `goal draft`, if this project wants
the section anyway; `hiraku-auto goal tidy` clears out what earlier runs left behind, and never
deletes a line a person wrote.

Where a draft came from is a different thing and is always kept: `## Grounding` lists the claims the
agent took from your files and the file each came from, replaced whole on every redraft rather than
appended to.

## Agent

`agent.command` is what gets spawned for every workflow. Precedence, highest first:
`--agent-cmd`, then `HIRAKU_AGENT_CMD`, then this, then `claude -p`. Put the flags this project
always needs here rather than retyping them:

    command: claude -p --allowedTools WebSearch,WebFetch,Read,Glob,Grep --add-dir ../<code-repo>

`--allowedTools` is not optional for `discover`. `claude -p` is non-interactive, so a tool it was
not given up front is requested and denied, and the workflow then cites what the model recalled
instead of what it read. Proposals from such a call are marked `**[UNVERIFIED: ...]**` in *Where it
came from*, and `proposal accept` says so again — but the fix is to name the tools here.

`agent.unset_env` lists variables dropped from the agent's environment for every call. The one
that matters is `ANTHROPIC_API_KEY`: while it is set, `claude` uses it in preference to your
claude.ai login, which bills the API account and stops your organisation's connectors loading. Set

    unset_env: [ANTHROPIC_API_KEY]

and every call here uses the login, while the key stays available to everything else on the
machine — nothing is written to your shell and the key is not touched. `--unset-env` on a single
call adds to this list; it never replaces it, because both are saying "drop this".

`agent.timeout` is the per-call ceiling in seconds. `--timeout` overrides it.

## Tracking

`.hiraku/runs.csv` is always written and is the source of truth: the promotion gate compares
`run_kind`, `lockfile_sha256`, `container_digest` and the goal record as it stood at the run's
commit, and MLflow has no column for any of those. So the backend is `table` or `table+mlflow` —
there is no `mlflow` alone, and `hiraku-auto check` and `promote` never need a network.

With `table+mlflow`, each finished run is also logged to MLflow: provenance as params, numeric
metrics as metrics, and `hiraku.run_id` / `hiraku.goal` / `hiraku.experiment_id` as tags. The
MLflow run id comes back into the `mlflow_run_id` column, so the two records point at each other.
A goal maps to an MLflow *experiment* — same idea, one set of comparable runs — using the goal id
unless the goal declares `mlflow_experiment:` in its front matter.

An experiment may narrow this to `table` in its `launch.yml`; it may never widen it.
Run `hiraku-auto tracking` to see what is resolved and whether the server answers.

## Bounds

The numbers in front matter, with the reasoning behind them. A bound without a reason gets raised
the first time it is inconvenient.

- `max_runs_per_goal` — why this number
- `max_tree_depth` — how deep a chain of diagnostics may go before a human looks
- `max_concurrent_worktrees` — what the machine can actually sustain
- `max_wallclock_hours_per_run` — the point past which a run should be killed and reconsidered

## Promotion

- `reproduction_seeds` — additional seeds required before a result reaches `leaderboard.md`
- `agreement_tolerance` — how far the reproduction may differ and still count
- Sign-off: who or what signs off in `guided` and in `auto`

## Context

The files in `context.sources` are excerpted into the agent's prompt for the `draft`, `design` and
`discover` workflows, so it knows this project's terms rather than asking you what they mean. Run
`hiraku-auto context` to see exactly what would be sent, and trim this list if anything there should
not leave the machine. `context: {sources: []}` turns it off entirely.

Point it at whatever already describes the project: the memory file the coding agent reads, the
README, design notes, a metric definitions page. Source trees are not swept — name a specific file
with `hiraku-auto agent design <goal> --context src/pipeline.py` when a module is the thing that
needs reading.

Sources may reach outside this project. That is the normal case: the research project holds the
records, and the code being studied lives in its own repository, so
`../mapillary_alignment/CLAUDE.md` is a sensible source. Credential locations (`.ssh`, `.env`,
`.netrc`, keyrings) are refused wherever they appear, and a bare home or filesystem root is
refused rather than swept.

## Data

Datasets, their locations and checksums, splits that must not be regenerated, and any restriction on
what may leave the machine or be sent to an external service.

## Permitted commands

The command families an auto loop may execute. Be specific; a wildcard here makes every other bound
decorative.
