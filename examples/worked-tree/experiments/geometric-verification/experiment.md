---
id: geometric-verification
branch: exp/geometric-verification
mode: guided
goal: same-place-verification
parent: retrieval-baseline
kind: hypothesis
status: concluded
verdict: supports
origin: researcher
expectation: "Two-view RANSAC on retrieved pairs raises verified precision above 0.95 at a recall cost under 0.03."
created: 2026-08-11
concluded: 2026-08-14
---

# Two-view geometric verification

## Scope: hypothesis or task

Responds to `retrieval-baseline`: the gap is in acceptance, so add a geometric consistency check
before accepting a retrieved pair.

## Expectation and how it could fail

False positives should mostly be pairs with no consistent two-view geometry, so requiring a minimum
inlier count should remove them cheaply. Predicted: precision above 0.95, recall cost under 0.03.

Refuted if precision stays below 0.95, or if reaching it costs more than 0.03 recall — the latter
would mean the check is rejecting true matches as readily as false ones.

## Implementation plan

Mutual-nearest correspondences on the existing descriptor, RANSAC for the fundamental matrix, accept
on a minimum inlier count. Threshold fixed at the literature-default 3.0 px for this node; tuning is
deliberately a separate question.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

`r-0003` (seed 1): `verified_precision` 0.961, `recall_at_1` 0.858 — precision above target, recall
cost 0.023, both inside the expectation.

`r-0004` (seed 2), the promotion reproduction: 0.957 / 0.861. Agreement 0.004, inside the goal's
0.01 tolerance.

`r-0005` (seed 3): 0.959 / 0.857.

## Conclusions

Expectation supported across three seeds. Promoted as the leader for this goal on `r-0003` with
`r-0004` as reproduction.

What this does not establish: the threshold was never varied, so how much of the margin depends on
3.0 px specifically is unknown, and the result is aggregate — it says nothing about whether
particular conditions still fail.

## Decisions and next steps

Ablate the threshold to find out whether the margin is real or a lucky default.

## Children

- `ransac-threshold-sweep` — ablation, concluded, inconclusive
