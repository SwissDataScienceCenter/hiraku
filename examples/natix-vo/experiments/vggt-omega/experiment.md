---
id: vggt-omega
branch: exp/vggt-omega
mode: guided
goal: vo-backend-selection
parent: occany-baseline
kind: hypothesis
status: approved
origin: researcher
expectation: "Global feed-forward pose estimation reduces accumulated drift relative to OccAny, lowering ate_rmse_m without reducing completion_rate."
created: 2026-08-24
---

# VGGT-Omega

## Scope: hypothesis or task

One of two parallel attempts to beat the OccAny baseline. Runs on its own branch and worktree
alongside `vggt-long`.

## Expectation and how it could fail

The mechanism to be tested is that estimating geometry over a set of frames jointly, rather than
accumulating frame-to-frame, removes the error accumulation that dominates long dashcam sequences.
So the prediction is specifically about `drift_pct` falling, with `ate_rmse_m` following.

Refuted if `drift_pct` does not fall — that would mean the accumulation is not where the error comes
from, which is worth knowing before trying `vggt-long`, since it shares the assumption.

Watch for a different outcome that is neither: `ate_rmse_m` improving while `completion_rate` drops.
That is not a win under this goal's gate, and it usually means the backend is declining the hard
sequences rather than solving them.

## Implementation plan

**[Fill in: how frames are batched or windowed, resolution, whether intrinsics are given or
estimated, and what happens at sequence boundaries.]**

The scale question matters here and should be settled explicitly: if this backend produces a
different scale convention from OccAny, the alignment step in the protocol — with or without scale —
decides whether the comparison is fair. Same procedure for both, decided at the goal.

## Evaluation protocol

Inherited, unchanged.

## Findings

**[Gap — no runs recorded.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

If `drift_pct` falls as predicted, ablate what causes it before promoting. If it does not, triage
before running `vggt-long`, since a shared assumption would have been refuted.

## Children

None yet.
