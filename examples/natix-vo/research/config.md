---
project: NATIX visual odometry and cross-domain alignment
active_goals: [vo-backend-selection, cross-domain-features]
bounds:
  max_runs_per_goal: 40
  max_tree_depth: 4
  max_concurrent_worktrees: 2
  max_wallclock_hours_per_run: 8
promotion:
  reproduction_seeds: 1
  agreement_tolerance: 0.05
---

# Research configuration

> **Seeded, not measured.** The two goals are the real open questions. Their trees are structured
> and their expectations are written, but **no run has been recorded** — the run table holds only a
> header, and every Findings section is a marked gap. Fill the bracketed fields below and the
> baselines' Findings from work already done, then launch. Nothing in this project is invented.

## Goals

- `vo-backend-selection` — which VO backend or strategy produces usable trajectories on NATIX
  dashcam video, and where the ones that fail actually fail.
- `cross-domain-features` — whether the Mapillary↔NATIX domain gap is what limits VGGT-based
  reconstruction, and whether DINOv3 features close it.

## Environment

- Build: `uv sync` — **[confirm: is this how the VO stack is built, or is there a separate
  environment per backend?]**
- Python: **[confirm]**
- Hardware assumed: **[confirm GPU model and memory — it bounds which backends can run at all, and
  `vggt-long` in particular may be memory-bound rather than accuracy-bound]**
- `accelerator` convention: `<count>x <device name> <memory>`, e.g. `1x NVIDIA A100 40GB`.
- Determinism: **[confirm]**. If any backend is non-deterministic at a fixed seed, say which — it
  sets how many seeds a comparison needs, and the promotion gate reads it.

Backends likely need conflicting dependency versions. That is the case worktrees exist for: one
checkout and one virtual environment per active experiment, on one Git repository. See
`docs/autoresearch/environment.md`.

## Bounds

- `max_runs_per_goal: 40` — **[adjust to available device time]**
- `max_tree_depth: 4` — four diagnostics deep without an answer means the setup is wrong, not that a
  fifth is needed.
- `max_concurrent_worktrees: 2` — assumes one GPU. Raise only if runs are not device-bound.
- `max_wallclock_hours_per_run: 8` — past this, kill and reconsider rather than wait.

## Promotion

- `reproduction_seeds: 1`
- `agreement_tolerance: 0.05` — **[in the primary metric's units; needs setting per goal once the
  seed-to-seed spread of the baseline is known. Setting it before that is guesswork, and it is the
  number the promotion gate depends on most.]**
- Sign-off: researcher in `guided`; in `auto`, the reproduction run plus a recorded promotion entry.

## Data

- NATIX sequences: **[paths, which segments, checksums]**
- Ground truth: **[GPS/RTK? what accuracy? this bounds every trajectory metric — a 5 m ground truth
  cannot validate a 2 m trajectory claim]**
- Mapillary reference: **[paths, coverage, licence constraints]**
- Splits are fixed once and never regenerated.

## Permitted commands

**[list the exact entrypoints an auto loop may run]**. Nothing that installs dependencies, writes
outside the project, or reaches the network.
