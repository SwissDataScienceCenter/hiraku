---
id: vggt-pose-consistency
branch: exp/vggt-pose-consistency
mode: auto
goal: same-place-verification
parent: retrieval-baseline
kind: hypothesis
status: running
origin: researcher
expectation: "Pose consistency from a feed-forward 3D model verifies pairs that two-view geometry rejects, raising recall at equal precision."
created: 2026-08-13
concluded:
---

# Pose consistency verification

## Scope: hypothesis or task

The second independent attempt from `retrieval-baseline`. Where `geometric-verification` accepts on
two-view inliers, this accepts on the consistency of a feed-forward pose estimate — which should
survive cases where sparse correspondence fails.

## Expectation and how it could fail

Predicted: recall above `geometric-verification`'s 0.858 at precision no lower than 0.95, because
the failure mode being addressed is correspondence sparsity rather than geometry.

Refuted if recall does not improve, or if precision falls below 0.95 — the latter meaning the pose
estimate is confidently wrong on the pairs that matter, which is worse than declining to answer.

## Implementation plan

Run the model on the retrieved pair, take relative pose, accept on a consistency threshold against
the retrieval hypothesis.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

`r-0010` (working-tree launch, seed 1): `verified_precision` 0.902, `recall_at_1` 0.871.
Non-reproducible — `run_kind: worktree` — so this is a debugging observation and not evidence. Not
eligible for promotion, and an execution defect cannot be ruled out from it.

`r-0011` (commit-backed, seed 1): 0.913 / 0.869. Recall is above `geometric-verification` as
predicted, but precision is 0.037 short of the 0.95 floor.

Both runs show precision degrading specifically where the two observations differ in scale, which
points at the pose estimate's scale rather than at its rotation.

## Conclusions

Open. The recall half of the expectation looks supported; the precision half does not, and the
pattern is specific enough to name a mechanism rather than treat as noise.

## Decisions and next steps

Diagnose the scale behaviour before running further seeds. Running more seeds of a configuration
whose failure mode is already localised would spend budget without discriminating anything.

## Children

- `depth-scale-drift` — diagnostic, proposed, awaiting approval
