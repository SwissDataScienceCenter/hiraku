---
id: vo-backend-selection
status: active
primary_metric: ate_rmse_m
direction: minimize
target:
---

# VO backend selection for NATIX video

## Question

Which visual-odometry backend or strategy produces usable trajectories on NATIX dashcam video — and
for those that do not, what specifically breaks?

The second half is not a courtesy. OccAny has been run and rig-camera VO gave no improvement; a
ranking that does not explain *why* the losers lose cannot tell you whether the next backend will
fail for the same reason.

## Why it matters

Everything downstream — mapping, alignment, place recognition — inherits the trajectory. A backend
choice made on one sequence, or on a metric that hides drift, propagates silently.

## Success criterion

**[Needs your number.]** Suggested shape, because a bare "lowest error wins" will mislead here:

- **Primary**: `ate_rmse_m` below the OccAny baseline by a stated margin, on the same segments.
- **Gate**: `completion_rate` no worse than the baseline. A backend that fails on a third of
  sequences and is excellent on the rest is not better; it is differently useful, and averaging over
  only its successes is the most common way a VO comparison becomes meaningless.
- **Reported alongside**: `drift_pct` (drift as a percentage of path length). ATE alone conflates a
  constant offset with accumulating drift, and the two have completely different fixes.

## Evaluation protocol

Fixed before the first run and inherited unchanged by every experiment under this goal. **Every
bracketed field needs filling before launching anything** — a protocol settled after the first
result is not a protocol.

- Sequences: **[which NATIX segments, by ID; how many; total length]**
- Ground truth and its accuracy: **[see config.md — this caps every claim made here]**
- Alignment before scoring: **[Umeyama with scale, or without? Monocular VO is scale-ambiguous, so
  this choice decides whether you are measuring shape or shape-and-scale. It must be identical
  across backends.]**
- `ate_rmse_m`: RMSE of position error after the alignment above, in metres.
- `drift_pct`: final position error as a percentage of trajectory length.
- `completion_rate`: fraction of sequences that produce a trajectory at all, with "failure" defined
  as **[tracking loss? non-finite output? a time limit?]**
- Stratification: report per condition — **[night, rain, low speed / stopped, tunnel, motorway?]**
  Aggregate-only reporting is what hides the failure modes this goal exists to find.
- Seeds: **[how many; see determinism in config.md]**
- Held fixed: input resolution, frame rate, intrinsics, and the alignment procedure.

## Out of scope

Changing the ground truth or the sequence set — both move the reference every comparison is measured
against. Downstream mapping quality; this goal ends at the trajectory.

## Open threads

- Whether the useful answer is one backend or a per-condition selection.
- Whether failures are shared across backends (a data property) or backend-specific (a method
  property). The stratified reporting above is what would tell you.

## History

- Goal opened on this branch. Protocol not yet fixed — bracketed fields outstanding.
