---
id: domain-gap-attribution
branch: exp/domain-gap-attribution
mode: guided
goal: cross-domain-features
parent: vggt-alignment-baseline
kind: diagnostic
status: approved
origin: agent-diagnostic
expectation: "Alignment error is systematically higher on cross-domain pairs than on within-domain pairs at matched difficulty, and the gap is concentrated in the feature-matching stage rather than in the pose solver."
created: 2026-08-24
---

# Is it actually the domain gap?

## Scope: hypothesis or task

Runs **before** `dinov3-features`, and this ordering is the point. The plan is to swap features
because the domain gap is suspected. If the limit is something else — pair difficulty, viewpoint
change, the pose solver, ground-truth accuracy — then a feature swap will not fix it, and a null
result from `dinov3-features` would be uninterpretable: no way to tell whether DINOv3 failed to close
the gap, or whether the gap was never the binding constraint.

Diagnosing first costs one cheap node and makes the expensive node's result meaningful either way.

## Expectation and how it could fail

Two things predicted, and they are separable:

1. **The gap exists**: `align_median_err_m` is materially worse cross-domain than within-domain at
   matched difficulty. Refuted if within-domain pairs are just as bad — then the limit is the
   pipeline or the pair difficulty, not the domain, and this goal's premise needs revisiting.
2. **It is a matching problem**: the degradation appears in correspondence quality — fewer, or
   systematically wrong, matches — rather than in the solver's behaviour given good matches.
   Refuted if correspondence quality is comparable across domains and the error appears only after
   the solver, which would point at geometry or scale rather than appearance.

Only if **both** hold is a feature swap the indicated intervention. If 1 holds and 2 does not, the
gap is geometric — different camera height, lens, viewpoint distribution — and a different feature
extractor is unlikely to help, while photometric or geometric normalisation might.

## Implementation plan

Reuse the baseline's outputs; no new reconstruction is needed. Compare, cross-domain against
within-domain at matched difficulty: match count and inlier ratio per pair, the spatial distribution
of matches, error before and after the solver, and error as a function of the appearance difference
between the two images **[exposure, colour statistics, time of day — define the measure]**.

## Evaluation protocol

Inherited, unchanged. Reads intermediate quantities plus the goal's metrics on the existing pair set
and its controls.

## Findings

**[Gap — approved, not run.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

Investigation test — all six hold:

1. **Named cause** — the domain gap acts through correspondence quality.
2. **Discriminating** — separates three explanations, not two: domain gap via appearance (feature
   swap indicated), domain gap via geometry (normalisation indicated, feature swap unlikely to help),
   and no domain effect at all (goal premise wrong). Each predicts a different pattern.
3. **Novel** — nothing has measured the within-domain control against cross-domain.
4. **In scope** — no change to the pair set, ground truth, or protocol.
5. **Within budget** — reuses existing outputs.
6. **Proportionate** — decides whether the expensive feature swap is the right intervention, and
   makes its result interpretable either way. Cheapest high-value node in this goal.

## Children

None.
