---
id: same-place-verification
status: active
primary_metric: verified_precision
direction: maximize
target: 0.95
---

# Same-place verification

> Illustrative protocol — see the placeholder notice in `research/config.md`.

## Question

Given two observations that a retrieval stage proposes as the same physical place, can we decide
correctly whether they are — at a precision high enough to be relied on downstream, without giving
up so much recall that the stage stops being useful?

## Why it matters

Retrieval alone proposes plausible matches; anything built on top inherits its false positives. A
verification stage that is right about the pairs it accepts is what makes the rest usable.

## Success criterion

`verified_precision >= 0.95` while `recall_at_1` stays within 0.03 of the retrieval-only baseline.
Precision bought by discarding most true matches does not answer the question.

## Evaluation protocol

Fixed before the first run; inherited unchanged by every experiment under this goal.

- Dataset: held-out sequence set, `sha256:0e91…` (illustrative)
- Splits: fixed query/reference partition, never regenerated
- `verified_precision`: fraction of accepted pairs that are true same-place, ground truth by surveyed
  pose within 5 m and 30 degrees
- `recall_at_1`, `recall_at_5`: unchanged from the retrieval-only definition, so the cost of
  verification is visible
- Seeds: 3 per configuration; report mean and spread
- Held fixed: descriptor, retrieval top-k, image resolution

## Out of scope

Changing the retrieval stage itself, and any change to the descriptor — both would move the baseline
this goal is measured against.

## Open threads

- Whether night-time sequences fail for reasons unrelated to viewpoint.
- Whether verification cost can be amortised across a sequence rather than paid per pair.

## History

- 2026-08-10 — goal opened, protocol fixed.
