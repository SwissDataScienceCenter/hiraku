---
id: depth-scale-drift
branch: exp/depth-scale-drift
mode: guided
goal: same-place-verification
parent: vggt-pose-consistency
kind: diagnostic
status: proposed
origin: agent-diagnostic
expectation: "The precision shortfall comes from scale drift in the predicted depth, not from rotation error; normalising scale per pair recovers the missing precision."
created: 2026-08-20
concluded:
---

# Depth scale drift diagnostic

## Scope: hypothesis or task

Explains `vggt-pose-consistency`'s precision shortfall. Both its runs degrade specifically on pairs
whose observations differ in scale, which is consistent with the predicted depth being correct up to
an unknown per-observation factor.

## Expectation and how it could fail

If scale is the cause, decomposing the consistency error into a scale component and a rotation
component should show the error concentrated in scale on exactly the failing pairs, and
scale-normalised consistency should recover precision to at least 0.95.

Refuted if the error is spread across rotation too, or concentrated in scale on pairs that do **not**
fail — either would mean scale is a correlate rather than the cause, and the shortfall lies elsewhere.

## Implementation plan

Decompose the existing consistency residual per pair without re-running the model; then re-accept
with a scale-normalised criterion.

## Evaluation protocol

Inherited from the goal, unchanged. Reuses `r-0011`'s outputs, so no new inference is needed.

## Findings

None yet — awaiting approval.

## Conclusions

None yet.

## Decisions and next steps

Investigation test, as recorded at proposal:

1. **Named cause** — per-observation scale ambiguity in predicted depth.
2. **Discriminating** — separates "scale ambiguity is the cause" from "rotation error is the cause";
   the first predicts residual concentrated in the scale component on failing pairs only, the second
   predicts it spread across both.
3. **Novel** — nothing in this goal's tree has examined the pose residual's composition.
   `ransac-threshold-sweep` concerns a different acceptance rule entirely.
4. **In scope** — inside the node's approved scope, protocol unchanged.
5. **Within budget** — 1 run of 12 remaining; depth 3 of 4.
6. **Proportionate** — reuses existing outputs, so cost is minutes, against a precision shortfall
   that currently blocks the node.

All six hold. Its parent is `guided`, so this child is `guided` too — a child may narrow its
parent's mode, never widen it — and it stays at `proposed` until the researcher approves it.

## Children

None.
