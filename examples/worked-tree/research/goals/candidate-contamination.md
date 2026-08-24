---
id: candidate-contamination
status: active
primary_metric: contaminated_frac
direction: minimize
target: 0.02
---

# Candidate contamination

> Illustrative protocol — see the placeholder notice in `research/config.md`.

## Question

How much of the retrieval candidate set consists of observations that are trivially the same place —
neighbours of the query in the same pass rather than genuine revisits — and how much of the reported
performance depends on them?

## Why it matters

If a meaningful share of accepted candidates are trivial, then every number measured on this data is
partly measuring the sampling of the data. That would make results under this protocol not wrong so
much as uninterpretable, which affects every other goal in the project.

## Success criterion

`contaminated_frac <= 0.02` under a stated exclusion rule, **and** a quantified statement of how the
goal's other metrics move once contamination is removed. A low number achieved by an exclusion rule
that also removes genuine revisits does not answer the question.

## Evaluation protocol

- Dataset: the same held-out sequence set as `same-place-verification`, `sha256:0e91…`
- `contaminated_frac`: share of accepted candidates whose ground-truth relationship to the query is
  same-pass adjacency rather than a revisit
- Reported alongside: `recall_at_1` before and after exclusion, so the cost of the rule is visible
- Ground truth: surveyed pose plus pass identity
- Seeds: 3; contamination is a property of the data, but the retrieval it is measured through is not
  deterministic
- Held fixed: descriptor, top-k, splits

## Out of scope

Changing the ground-truth definition of a revisit. That is a dataset question, and changing it would
make every measurement here incomparable with the rest of the project.

## Open threads

- Whether an exclusion rule can be stated geometrically rather than temporally.
- Whether contamination is concentrated in particular sequences.

## History

- 2026-08-18 — goal opened, protocol fixed.
