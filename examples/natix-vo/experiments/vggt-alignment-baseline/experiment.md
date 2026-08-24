---
id: vggt-alignment-baseline
branch: exp/vggt-alignment-baseline
mode: guided
goal: cross-domain-features
kind: baseline
status: concluded
verdict:
origin: researcher
expectation: "VGGT-based reconstruction registers Mapillary-NATIX pairs at an accuracy and success rate that define the reference for any feature change."
created: 2026-08-24
concluded:
---

# VGGT alignment baseline

## Scope: hypothesis or task

Root of `cross-domain-features`. The current state: VGGT reconstruction is not accurate enough for
Mapillary↔NATIX alignment.

## Expectation and how it could fail

As a baseline, that it runs and gives stable numbers to compare against.

## Implementation plan

Already implemented. **[Record the pipeline: how pairs are formed, what VGGT is given, how the
registration is solved, and where the current failures show up.]**

## Evaluation protocol

Inherited from `cross-domain-features`, unchanged — including the within-domain control pairs, which
must be run here too. The baseline's cross-domain-versus-within-domain difference *is* the measurement
that `domain-gap-attribution` interprets.

## Findings

**[Gap — fill from existing work.]** Needed: `align_median_err_m` and `align_success_rate` on
cross-domain pairs and on the within-domain controls, the gross-failure rate, and a characterisation
of the failures — do they fail to register at all, or register confidently to the wrong place? Those
two have different causes and the second is much more dangerous downstream.

## Conclusions

**[Gap.]** `verdict` left blank while `status` is `concluded`; the viewer flags it.

## Decisions and next steps

Attribute the cause before changing the features. See `domain-gap-attribution`.

## Children

- `domain-gap-attribution` — diagnostic, approved
- `dinov3-features` — hypothesis, proposed
