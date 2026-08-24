---
id: sequence-level-dedup
goal: candidate-contamination
origin: agent-literature
proposed_parent:
kind: hypothesis
status: proposed
proposed_on: 2026-08-22
decided_on:
---

# Sequence-level deduplication before retrieval

## Hypothesis

Contamination in this project is structural — a consequence of indexing every frame of every pass —
so deduplicating to one representative per pass per place before retrieval removes it without any
per-candidate exclusion rule, and therefore without the recall trade-off both existing nodes pay.

## Where it came from

Two sources, and what each actually claims:

- A place-recognition benchmarking paper reporting that per-frame indexing of repeated traversals
  inflates retrieval metrics, and that deduplicating the index changes reported recall by several
  points. **Claimed under**: a different dataset, with denser revisits than ours, and a
  deduplication rule tied to that dataset's pass annotations. **Strength**: a single dataset, one
  team, not independently reproduced as far as could be established.
- A blog post from a mapping team describing the same practice as routine engineering. **Claimed
  under**: no quantitative evaluation at all. **Strength**: a practitioner report, useful as evidence
  that the approach is workable in production, not as evidence of an effect size.

Neither source's result is a finding of this project, and neither has been measured under our
protocol.

## Why it applies here

The bridge is specific and testable: our passes are separable from vehicle trajectory, and
`temporal-adjacency-exclusion` has already established that our residual contamination is
concentrated in slow and stopped traffic — which is exactly where per-frame indexing produces the
most near-duplicates per metre travelled. So the mechanism the sources describe is present in our
data, and we have already measured its footprint.

Where the bridge is weaker: our revisit density is lower than the paper's, so the headroom may be
smaller, and our passes are inferred rather than annotated, which means the segmentation itself could
introduce error the sources did not face.

## What it predicts

`contaminated_frac` at or below 0.02 with no exclusion rule applied, and a recall cost below the
0.028 that the 60 s temporal window paid. Refuted if contamination persists after deduplication —
which would mean it does not come from within-pass redundancy — or if representative selection loses
genuine revisits.

## Investigation test

1. **Named cause** — within-pass frame redundancy in the index, concentrated at low speed.
2. **Discriminating** — separates "contamination is structural, from the index" from "contamination
   is a candidate-selection problem". The first predicts the target is reached with no exclusion
   rule; the second predicts contamination persists and the existing rules remain necessary.
3. **Novel** — nothing in this goal's tree touches the index. `temporal-adjacency-exclusion` and
   `gps-radius-exclusion` both filter after retrieval. Checked all nodes and the declined list.
4. **In scope** — changes the index only. Ground truth, splits, and the revisit definition are
   untouched, so comparisons stay valid.
5. **Within budget** — 3 runs; 9 of 40 used for this goal; depth 1.
6. **Proportionate** — if it holds, both candidate-level rules become unnecessary, so it either
   simplifies the goal or rules out the structural explanation. Either outcome is informative.

## Cost

3 runs, roughly 2 hours each. No new dependencies, no new data — pass segmentation uses trajectory
already available. An index rebuild is required, which is why this needs a decision rather than
proceeding within budget.

## What it would take to decline

The strongest case against: `gps-radius-exclusion` is already approved and tests a cheaper
hypothesis that could reach the same target, and it will report first. If a distance radius reaches
0.02 at low recall cost, the goal is answered and this proposal's extra complexity — inferred pass
segmentation, a rebuilt index, a representative-selection rule that is itself a new source of error —
buys nothing. Sequencing this after `gps-radius-exclusion` rather than alongside it would cost
nothing and could make it unnecessary.

A second argument: our revisit density is materially lower than the source paper's, so the effect
size may be too small to measure at 3 runs, which would produce an inconclusive node.

## Decision

Pending. Recommended: **defer** until `gps-radius-exclusion` reports, on the reasoning above.
