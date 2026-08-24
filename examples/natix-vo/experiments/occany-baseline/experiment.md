---
id: occany-baseline
branch: exp/occany-baseline
mode: guided
goal: vo-backend-selection
kind: baseline
status: concluded
verdict:
origin: researcher
expectation: "OccAny produces trajectories on NATIX video at an accuracy and completion rate that define the reference every other backend is measured against."
created: 2026-08-24
concluded:
---

# OccAny baseline

## Scope: hypothesis or task

Root of this goal. OccAny has already been run; this node exists to make that the recorded reference
rather than remembered context, so every later comparison has something fixed to be measured against.

## Expectation and how it could fail

A baseline's expectation is weaker than a hypothesis's: that it runs, and that its numbers are stable
enough to compare against. It "fails" only if its own seed-to-seed spread is large relative to the
differences between backends — in which case no comparison under this goal can be settled at one seed
per configuration, and the seed count in the protocol has to go up before anything else runs.

## Implementation plan

Already implemented. Record the entrypoint in `launch.yml` so the reference is re-runnable rather
than a number in a notebook.

## Evaluation protocol

Inherited from `vo-backend-selection`, unchanged.

## Findings

**[Gap — fill from the OccAny runs already done.]** For each: `ate_rmse_m`, `drift_pct`,
`completion_rate`, per-condition breakdown, and the seed-to-seed spread. Where the numbers were
produced outside Hiraku, say so and mark them non-reproducible; they can serve as the reference
without being promotable.

The seed spread is the most important number on this page. It sets `agreement_tolerance` in
`research/config.md` and decides how many seeds every later comparison needs.

## Conclusions

**[Gap.]** Once filled: the reference values, the conditions where OccAny already fails, and whether
its variance permits single-seed comparisons.

`verdict` is deliberately blank while `status` is `concluded` — the viewer flags this, correctly. Set
it when the Findings are in.

## Decisions and next steps

Two backends to try in parallel (`vggt-omega`, `vggt-long`), plus the already-attempted
`rig-camera-vo` recorded as a third child so its negative result is part of the tree rather than
folklore.

## Children

- `vggt-omega` — hypothesis, approved
- `vggt-long` — hypothesis, approved
- `rig-camera-vo` — hypothesis, concluded, refutes
