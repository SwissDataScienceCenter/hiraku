---
id: dinov3-features
branch: exp/dinov3-features
mode: guided
goal: cross-domain-features
parent: vggt-alignment-baseline
kind: hypothesis
status: proposed
origin: researcher
expectation: "DINOv3 features are more invariant to the Mapillary-NATIX appearance gap than the current features, lowering align_median_err_m without lowering align_success_rate."
created: 2026-08-24
---

# DINOv3 features

## Scope: hypothesis or task

The intervention the goal was opened for. Held at `proposed` rather than `approved` deliberately: it
should start after `domain-gap-attribution` reports, because that result determines whether this is
the right intervention and how its outcome should be read.

This is not gatekeeping for its own sake. If this runs first and comes back null, you cannot
distinguish "DINOv3 does not help" from "the gap was not the constraint" — and that ambiguity costs
more than the cheap diagnostic does.

## Expectation and how it could fail

Self-supervised features trained across diverse imagery should be less sensitive to exposure, colour
response and seasonal appearance than features tuned on a narrower distribution. So the prediction is
specifically about *appearance* invariance, and it should show up as improvement concentrated on the
pairs with the largest appearance difference — not as a uniform shift across all pairs.

That concentration is the real test. A uniform improvement across all pairs, including within-domain
controls, means the new features are simply better in general and says nothing about the domain gap —
still useful, but a different finding, and it should be recorded as such rather than as confirmation.

Refuted if error does not fall on high-appearance-difference pairs. Watch the failure mode where
`align_median_err_m` improves while `align_success_rate` drops: registering fewer pairs more
accurately is not a win under this goal's gate.

## Implementation plan

**[Fill in: which DINOv3 variant and resolution, where features enter the pipeline — dense
correspondence, or a re-ranking stage, or replacing VGGT's internal features — and what else must stay
identical for the comparison to hold.]**

The integration point is the main design risk. Replacing only the matching features while keeping
VGGT's geometry is a different experiment from replacing the reconstruction backbone, and they should
not be conflated in one node. If both are wanted, they are two children.

## Evaluation protocol

Inherited, unchanged. Both metrics computed over the same pair set as the baseline, including pairs
that fail, and reported stratified by appearance difference.

## Findings

**[Gap — proposed, not run.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

**[Pending `domain-gap-attribution`.]** If that node finds the gap is geometric rather than
appearance-based, revise this expectation or replace this node with a normalisation experiment before
spending the runs.

## Children

None yet.
