---
id: gps-radius-exclusion
branch: exp/gps-radius-exclusion
mode: auto
goal: candidate-contamination
parent: temporal-adjacency-exclusion
kind: diagnostic
status: approved
origin: agent-diagnostic
expectation: "Excluding candidates within a distance radius of the query, rather than a time window, separates same-pass neighbours from genuine revisits and reaches contaminated_frac under 0.02 at under 0.01 recall cost."
created: 2026-08-21
concluded:
---

# Distance-radius exclusion

## Scope: hypothesis or task

Responds to `temporal-adjacency-exclusion`'s refutation. Its residual contamination sat in slow and
stopped traffic, where time and distance decouple; excluding on along-track distance should not have
that failure mode.

## Expectation and how it could fail

A distance radius should remove same-pass neighbours regardless of speed, so contamination should
reach 0.02 at a recall cost well under the 0.028 the 60 s window paid.

Refuted if contamination stays above 0.02, or if the recall cost matches the temporal rule — which
would mean the problem is the exclusion framing itself, not the variable, and would make a
sequence-level approach the only remaining option.

## Implementation plan

Exclude candidates within along-track distance r of the query. Radii 25 m, 50 m, 100 m, at the
goal's three seeds.

## Evaluation protocol

Inherited from the goal, unchanged.

## Findings

None yet — approved, not launched.

## Conclusions

None yet.

## Decisions and next steps

Investigation test, as recorded at proposal:

1. **Named cause** — time proxies along-track distance badly at low speed, which is precisely where
   the parent's residual contamination is concentrated.
2. **Discriminating** — separates "the variable was wrong" from "exclusion at candidate level cannot
   work"; the first predicts target reached at low recall cost, the second predicts the same
   trade-off the parent hit.
3. **Novel** — no node has excluded on distance. The parent tested time only.
4. **In scope** — the exclusion rule is what this goal is about; the ground-truth revisit definition
   is untouched.
5. **Within budget** — 9 runs of 40 used for this goal.
6. **Proportionate** — the outcome determines whether the goal continues at candidate level or moves
   to sequence level, so either result changes the plan.

## Children

None.
