---
id: rig-overlap-geometry
branch: exp/rig-overlap-geometry
mode: guided
goal: vo-backend-selection
parent: rig-camera-vo
kind: diagnostic
status: proposed
origin: agent-diagnostic
expectation: "The rig added no improvement because its cameras share too little field of view, and its baseline is too short relative to scene depth, to contribute constraint beyond what one camera already provides."
created: 2026-08-24
---

# Rig overlap and baseline geometry

## Scope: hypothesis or task

Explanation 2 for `rig-camera-vo`'s refutation, and the one that would mean the direction is not
worth pursuing at all — as opposed to being fixable.

A rig helps through shared observations. If the cameras point in substantially different directions
with little overlap, there are few shared features to constrain. And even with overlap, a baseline of
tens of centimetres against scene depths of tens of metres gives a depth precision far worse than
what the vehicle's own motion between frames provides — so the rig's stereo constraint is dominated
by the monocular one and contributes nothing measurable.

This is a geometry argument, so it can be settled largely by measurement rather than by more runs.

## Expectation and how it could fail

If this is the cause: the number of features observed by more than one camera per frame will be a
small fraction of the total, and the baseline-to-depth ratio will imply a depth uncertainty larger
than the frame-to-frame motion already achieves. Restricting to the subset of frames and scenes where
overlap *is* substantial — **[which conditions? tight turns, close structures, junctions?]** — should
then show the rig helping there, even though the aggregate is flat.

Refuted if overlap is in fact substantial and the baseline-to-depth ratio is favourable, and the rig
still adds nothing. Then geometry is not the limit and `rig-calibration-audit` is the explanation.

**Discriminating**: shared-feature counts and the baseline-to-depth ratio are properties of the rig
and the scenes, measurable without re-running the backend, and they separate "cannot help" from
"should help but is mis-calibrated".

## Implementation plan

Measure per frame: features observed by two or more cameras as a fraction of the total; the effective
baseline between overlapping pairs; the depth distribution of shared features; and the implied depth
uncertainty against the equivalent from frame-to-frame motion. Then look for the subset of frames
where the rig geometry is favourable and compare the rig against single-camera on that subset only.

Note the subset comparison is a smaller sample, so it answers "does it ever help" and not "how much
overall".

## Evaluation protocol

Inherited, unchanged. The subset comparison is reported as a subset, not as a headline number — it
cannot be promoted, since it is not the goal's pair set.

## Findings

**[Gap — proposed, not run.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

Investigation test — as for the sibling, conditions 1, 3, 4, 5, 6 hold and 2 is answered; the parent's
Findings decide which sibling runs first.

If this one is confirmed, the honest conclusion is that rig-camera VO cannot help with this rig
geometry, and the direction closes with a recorded reason rather than staying an open "maybe with more
experiments". That is a valuable outcome, and it is what stops the same idea returning later.

## Children

None.
