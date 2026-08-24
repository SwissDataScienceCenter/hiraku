---
id: ransac-threshold-sweep
branch: exp/ransac-threshold-sweep
mode: guided
goal: same-place-verification
parent: geometric-verification
kind: ablation
status: concluded
verdict: inconclusive
origin: researcher
expectation: "Verified precision varies materially with the RANSAC threshold, so 3.0 px is a tunable choice rather than a plateau."
created: 2026-08-14
concluded: 2026-08-16
---

# RANSAC threshold ablation

## Scope: hypothesis or task

Isolates one component of `geometric-verification`: how much of its margin depends on the 3.0 px
default.

## Expectation and how it could fail

Precision should vary by more than seed noise across 1.5–6.0 px, identifying a better operating
point.

Refuted if the curve is flat relative to seed spread, meaning the default sits on a plateau and the
threshold is not worth tuning.

## Implementation plan

Four thresholds — 1.5, 3.0, 4.5, 6.0 px — each at one seed, everything else fixed.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

`r-0006`–`r-0009`: precision 0.948 (1.5 px), 0.961 (3.0), 0.957 (4.5), 0.939 (6.0). Spread across
thresholds is 0.022; seed spread at 3.0 px alone was 0.004 across three runs — but each threshold
here was run at a single seed, so the comparison between thresholds is one draw against another.

## Conclusions

Inconclusive, not refuted. The competing explanations the evidence cannot separate: a genuine broad
optimum between 3.0 and 4.5 px, versus seed noise at each point large enough to produce this
ordering by chance. One seed per threshold cannot distinguish them, and reading the 3.0 px seed
spread as if it applied to the others assumes the variance is threshold-independent, which is exactly
what is unknown.

A child was considered — three seeds at each of 3.0 and 4.5 px — and rejected on the
**proportionate** condition: eight further runs to choose between two points whose difference is at
most 0.004 above target, on a node already above target. The remaining budget is better spent on
`vggt-pose-consistency`.

What would change the answer: if precision were the binding constraint downstream rather than
comfortably above target, or if the ordering here mattered for a different dataset.

## Decisions and next steps

Keep 3.0 px. Threshold tuning is closed for this goal unless precision becomes binding.

## Children

None.
