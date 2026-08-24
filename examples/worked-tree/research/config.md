---
project: Place recognition on dashcam sequences
active_goals: [same-place-verification, candidate-contamination]
bounds:
  max_runs_per_goal: 40
  max_tree_depth: 4
  max_concurrent_worktrees: 3
  max_wallclock_hours_per_run: 6
promotion:
  reproduction_seeds: 1
  agreement_tolerance: 0.01
---

# Research configuration

> **Placeholder notice.** The two goals below are named after two open questions carried over from
> earlier work on this project, but their protocols, metrics, targets, and numbers in this example
> are **illustrative** and have not been confirmed against the real study. They exist to exercise
> the tree, the triage protocol, and the dashboard. Replace the two goal records and the run table
> before using this as anything other than a structural example.

## Goals

- `same-place-verification` — deciding whether two observations show the same physical place.
- `candidate-contamination` — how far retrieval candidate sets are polluted by observations that are
  trivially the same place, and what that does to reported numbers.

## Environment

- Build: `uv sync`
- Python: `>=3.14,<3.15`
- Hardware assumed: one CUDA device, 40 GB; runs that need more must say so in their record.
- `accelerator` convention: `<count>x <device name> <memory>`, e.g. `1x NVIDIA A100 40GB`.
- Determinism: not bit-reproducible. Feature extraction is deterministic at a fixed seed; RANSAC and
  any sampling are not. Every comparison therefore reports across seeds, and single-seed results are
  candidates rather than results.

## Bounds

- `max_runs_per_goal: 40` — roughly two weeks of the available device at typical run length.
- `max_tree_depth: 4` — a chain of four diagnostics without an answer means the setup is wrong, not
  that a fifth is needed.
- `max_concurrent_worktrees: 3` — three checkouts fit in disk and one device time-slices acceptably.
- `max_wallclock_hours_per_run: 6` — past this, kill and reconsider the run rather than waiting.

## Promotion

- `reproduction_seeds: 1` — one additional seed, because seed variance here is the dominant
  uncertainty.
- `agreement_tolerance: 0.01` — absolute, on the goal's primary metric.
- Sign-off: researcher in `guided`; in `auto`, the reproduction run plus a recorded promotion entry.

## Data

Sequences and their splits are fixed and checksummed per goal record. Splits are never regenerated —
regenerating them silently invalidates every comparison in the tree. Imagery stays on the machine.

## Permitted commands

`uv run python -m pr.*` entrypoints declared in each experiment's `launch.yml`, plus read-only git.
Nothing that writes outside the project, installs dependencies, or reaches the network.
