---
id: rig-camera-vo
branch: exp/rig-camera-vo
mode: guided
goal: vo-backend-selection
parent: occany-baseline
kind: hypothesis
status: concluded
verdict: refutes
origin: researcher
expectation: "Using the multi-camera rig jointly rather than a single camera adds geometric constraint and lowers ate_rmse_m relative to the single-camera baseline."
created: 2026-08-24
concluded: 2026-08-24
---

# Rig-camera VO

## Scope: hypothesis or task

Already attempted, and gave no improvement. Recorded as a concluded child of the baseline so the
negative result is in the tree — and so the two candidate explanations for it become experiments
rather than a conversation.

## Expectation and how it could fail

More cameras with known relative pose should over-constrain the motion estimate and resolve scale
directly, so error should fall. It was refuted: no improvement.

## Implementation plan

Already implemented. **[Record what was run: which cameras, how extrinsics were obtained, whether
frames were synchronised and how, and whether scale was taken from the rig baseline or still
estimated.]**

## Evaluation protocol

Inherited, unchanged.

## Findings

**[Gap — fill from the runs already done.]** The specific things needed to tell the two explanations
below apart:

- Was the result no *better*, or actually *worse*? Worse points at a bad calibration or
  synchronisation injecting error. Merely equal points at the rig adding no usable constraint.
- Did per-condition behaviour change at all, even with the aggregate flat? A rig should help most
  where a single camera is weakest — low texture, low speed. No change anywhere is a different
  finding from a redistribution that averages out.
- What did the multi-camera reprojection residuals look like relative to the single-camera ones?

## Conclusions

Class B — expectation refuted. The implementation ran and the protocol was followed; the predicted
improvement did not appear. This is a result, not a defect to repair, and it stands on its own.

Whether it is worth investigating further depends on the Findings above. Two mechanisms could produce
"no improvement", they have different fixes, and they are distinguishable — which is what justifies
the two diagnostic children rather than a general "try more rig configurations".

## Decisions and next steps

Two competing explanations, one diagnostic each. Both are proposed, not approved: they need the
Findings filled in first, because which one to run — or whether either is worth it — depends on
whether the result was flat or worse.

## Children

- `rig-calibration-audit` — diagnostic, proposed
- `rig-overlap-geometry` — diagnostic, proposed
