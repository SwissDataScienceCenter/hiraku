---
id: sequence-level-dedup
branch: exp/sequence-level-dedup
mode: guided
goal: candidate-contamination
kind: hypothesis
status: proposed
origin: agent-literature
expectation: "Deduplicating at sequence level before retrieval removes same-pass contamination structurally, so no per-candidate exclusion rule is needed."
created: 2026-08-22
concluded:
---

# Sequence-level deduplication

## Scope: hypothesis or task

A second root for this goal, from reading rather than from the project's own evidence — see
`research/proposals/sequence-level-dedup.md`. Where both existing nodes filter candidates after
retrieval, this changes what enters the index: one representative per pass per place.

## Expectation and how it could fail

If contamination is structural — an artefact of indexing every frame of every pass — then removing
the duplication at index time should drive `contaminated_frac` to near zero without any exclusion
rule, and so without the recall trade-off both candidate-level rules pay.

Refuted if contamination persists after deduplication, meaning it does not come from within-pass
redundancy; or if choosing a representative loses genuine revisits, trading the problem for a
different one.

## Implementation plan

Segment sequences into passes, cluster within a pass by pose, index one representative per cluster,
then measure contamination with no exclusion rule at all.

## Evaluation protocol

Inherited from the goal, unchanged. Note that this changes the index, not the ground truth or the
splits — the comparison stays valid.

## Findings

None yet — proposed, awaiting a decision.

## Conclusions

None yet.

## Decisions and next steps

Awaiting the researcher's decision on the proposal. Because its `origin` is `agent-literature`, it
requires a human decision regardless of mode: it imports a claim the project has not verified.

## Children

None.
