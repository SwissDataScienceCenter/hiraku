---
id: cross-domain-features
status: active
primary_metric: align_median_err_m
direction: minimize
target:
---

# Cross-domain features for Mapillary–NATIX alignment

## Question

VGGT-based reconstruction is not accurate enough for Mapillary↔NATIX alignment, and the suspected
cause is the domain gap between the two sources. Two questions, in order:

1. Is the domain gap actually what limits it?
2. If so, do DINOv3 features close enough of it to matter?

## Why it matters

The alignment is the bridge between the two datasets. Its error floor bounds anything built on the
correspondence between them.

## Success criterion

**[Needs your number.]** Suggested shape:

- **Primary**: `align_median_err_m` below the VGGT baseline by a stated margin, on the same pairs.
- **Gate**: `align_success_rate` no worse than the baseline — a feature swap that registers fewer
  pairs more accurately has moved the problem, not solved it.
- **Median rather than mean**, because a handful of gross misregistrations otherwise dominate and
  make the number a failure count rather than an accuracy measure. Report the gross-failure rate
  separately.

## Evaluation protocol

- Pair set: **[how many Mapillary↔NATIX pairs, selected how? Selection is the whole risk here — a
  set chosen by what VGGT already handles cannot show a domain-gap effect]**
- **Within-domain control pairs**: NATIX↔NATIX and Mapillary↔Mapillary at matched difficulty.
  Required, not optional. Without a control there is no way to separate "cross-domain is hard" from
  "this scene type is hard", and the whole first question is unanswerable.
- Difficulty matching: **[by viewpoint change, baseline distance, season, time of day?]**
- Ground truth: **[surveyed pose? GPS? and its accuracy]**
- `align_median_err_m`: median position error of the registered pair, in metres.
- `align_success_rate`: fraction of pairs registered at all, "registered" meaning **[criterion]**.
- Held fixed: reconstruction pipeline other than the feature stage, resolution, and the pair set.

Note that a feature swap changes correspondences, which changes which pairs register at all. So
`align_median_err_m` must always be read together with `align_success_rate`, and both computed over
the **same** pair set including the ones that failed.

## Out of scope

Changing the pair set or the ground truth once fixed. Improving the reconstruction backend itself —
that is `vo-backend-selection`'s territory and would confound this comparison.

## Open threads

- Whether the gap is appearance (colour, exposure, camera response) or geometry (viewpoint, height,
  lens) — these have different fixes, and a feature swap only addresses the first.
- Whether a cheaper intervention (photometric normalisation, exposure matching) captures most of the
  available gain. Worth knowing before committing to a backbone swap.

## History

- Goal opened on this branch. Protocol not yet fixed — bracketed fields outstanding.
