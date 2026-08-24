---
id: vggt-long
branch: exp/vggt-long
mode: guided
goal: vo-backend-selection
parent: occany-baseline
kind: hypothesis
status: approved
origin: researcher
expectation: "Explicit long-sequence handling keeps drift_pct roughly flat as sequence length grows, where OccAny's grows with length."
created: 2026-08-24
---

# VGGT-Long

## Scope: hypothesis or task

The second parallel attempt on the baseline. Distinct from `vggt-omega` in what it claims: not lower
error in general, but error that stops growing with sequence length.

## Expectation and how it could fail

This is a prediction about a *slope*, not a level, and it needs a different read of the same runs:
`drift_pct` plotted against sequence length, for this backend and for the baseline. A single averaged
number over mixed-length sequences cannot show it, and would let a real effect disappear into the
mean.

Refuted if the slope matches the baseline's. Note the likely confound before it happens: if long
sequences are also the ones that fail to complete, then the surviving long sequences are the easy
ones and the slope looks better for a reason that has nothing to do with the method. Read the slope
only over sequences that completed under **both** backends.

Also expect a resource ceiling rather than an accuracy result: if the sequence length this handles is
bounded by device memory, the honest finding is where that bound sits, and that is a `refutes` on the
expectation as written, not an execution defect.

## Implementation plan

**[Fill in: window or chunk size, how chunks are stitched, memory ceiling, and the longest sequence
that fits on the available device.]**

## Evaluation protocol

Inherited, unchanged, with one addition specific to this node: results are read as `drift_pct`
against sequence length, over the sequences that completed under both this backend and the baseline.
This does not change the metric or the pair set, so the comparison stays valid.

## Findings

**[Gap — no runs recorded.]**

## Conclusions

**[Gap.]**

## Decisions and next steps

**[Pending.]**

## Children

None yet.
