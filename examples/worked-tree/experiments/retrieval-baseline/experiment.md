---
id: retrieval-baseline
branch: exp/retrieval-baseline
mode: guided
goal: same-place-verification
kind: baseline
status: concluded
verdict: supports
origin: researcher
expectation: "Retrieval alone reaches high recall but precision well below the goal's target, establishing the gap verification must close."
created: 2026-08-10
concluded: 2026-08-11
---

# Retrieval-only baseline

## Scope: hypothesis or task

Establish the reference this goal is measured against: top-k retrieval with no verification stage.

## Expectation and how it could fail

Recall@1 should be high — the descriptor is known to work on this kind of imagery — while precision
on accepted pairs should sit well under 0.95, since nothing rejects a plausible-but-wrong match.

Refuted if precision were already at or near target, which would make the goal moot.

## Implementation plan

Fixed descriptor, top-k retrieval, accept the top-1 candidate. No verification.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

`r-0001` and `r-0002` (seeds 1, 2): `recall_at_1` 0.884 / 0.879, `verified_precision` 0.712 / 0.706.
Precision is 24 points below target with recall already high, so the gap is in acceptance, not in
retrieval.

## Conclusions

Expectation supported. The baseline is `verified_precision = 0.709` (mean of two seeds) at
`recall_at_1 = 0.881`. Any verification stage is measured against these two numbers together.

## Decisions and next steps

Two independent attempts at closing the gap, run in parallel: two-view geometric verification, and
pose consistency from a 3D foundation model.

## Children

- `geometric-verification` — hypothesis, concluded, supports
- `vggt-pose-consistency` — hypothesis, running
