---
id: temporal-adjacency-exclusion
branch: exp/temporal-adjacency-exclusion
mode: auto
goal: candidate-contamination
kind: baseline
status: concluded
verdict: refutes
origin: researcher
expectation: "Excluding candidates within a fixed time window of the query removes most contamination and brings contaminated_frac under 0.02."
created: 2026-08-18
concluded: 2026-08-21
---

# Temporal adjacency exclusion

## Scope: hypothesis or task

Root of this goal. The simplest exclusion rule: drop candidates recorded within a fixed time window
of the query, on the assumption that same-pass neighbours are close in time.

## Expectation and how it could fail

A 30 s window should remove most same-pass neighbours and bring `contaminated_frac` under 0.02 at
little recall cost.

Refuted if contamination stays above 0.02 at any window that does not also remove genuine revisits.

## Implementation plan

Filter candidates on timestamp difference before acceptance. Windows 10 s, 30 s, 60 s.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

`r-0012` (30 s): `contaminated_frac` 0.061, `recall_at_1` 0.872 — down from 0.094 unfiltered, well
short of 0.02.

`r-0013` (60 s): 0.058, `recall_at_1` 0.844. Contamination barely moves while
recall falls 0.028, so the window is now removing genuine revisits.

The residual contamination is concentrated in slow and stopped traffic, where the vehicle covers
little ground over a long interval. Time is a poor proxy for distance exactly where contamination is
worst.

## Conclusions

Expectation refuted, and this is a result rather than a problem to fix. Time-based exclusion cannot
reach the target: the interval needed to remove slow-traffic neighbours is long enough to remove real
revisits, and the two cannot be separated on a timestamp.

This is class B — the implementation is sound and the protocol was followed. The rule genuinely does
not work.

What would change the answer: nothing about this rule. A different exclusion variable is required,
which is a child, not a repair.

## Decisions and next steps

The named cause — time proxies distance badly at low speed — points directly at excluding on
distance instead. That child is justified; further windows are not.

## Children

- `gps-radius-exclusion` — diagnostic, approved
