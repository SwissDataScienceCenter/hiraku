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
  max_bytes_per_file: 20000
  max_total_bytes: 200000
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
