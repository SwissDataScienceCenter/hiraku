# Leaderboard

Promoted results only. Append-only; superseded leaders move to the goal's Superseded table.
Promotion requires all five conditions in `docs/autoresearch/research-loop.md`: better on the goal's
primary metric, `run_kind: commit`, unmodified protocol, independent reproduction within tolerance,
and sign-off.

---

## Goal: `same-place-verification`

Primary metric: `verified_precision` (maximize) · Target: 0.95

### Current leader

| Field | Value |
|---|---|
| Experiment | `geometric-verification` |
| Metric | `verified_precision = 0.961` |
| Run | `r-0003` |
| Commit | `7b3e5c81f09a2d64e5178cbb3a90f4d2e6187c0a` |
| Reproduction | `r-0004` at seed 2, `verified_precision = 0.957` (Δ 0.004, tolerance 0.01) |
| Recall cost | `recall_at_1` 0.858 vs. baseline 0.881 (Δ 0.023, limit 0.03) |
| Promoted | 2026-08-14 |
| Signed off by | researcher (guided) |

**Why it holds.** Requiring consistent two-view geometry rejects retrieved pairs that have no
plausible geometric relationship. Against `retrieval-baseline`, precision moves 0.709 → 0.961 for
0.023 of recall, so the comparison establishes that the baseline's errors were predominantly
geometrically inconsistent rather than genuinely ambiguous.

**What it does not establish.** The threshold was fixed at 3.0 px and never varied here;
`ransac-threshold-sweep` looked and came back inconclusive, so how much of the margin depends on that
specific value is unknown. The number is also an aggregate — it says nothing about whether particular
conditions still fail, and `vggt-pose-consistency` exists because sparse-correspondence failures are
suspected to persist inside it.

### Superseded

| Date | Experiment | Metric | Run | Superseded by |
|---|---|---|---|---|
| — | — | — | — | — |

### Not promoted, worth knowing

| Experiment | Result | Failed condition |
|---|---|---|
| `vggt-pose-consistency` | `recall_at_1` 0.869 at `verified_precision` 0.913 (`r-0011`) | (1) — recall is above the leader but precision is below the goal's floor, so it is not better on the goal's terms. |
| `vggt-pose-consistency` | `verified_precision` 0.902 (`r-0010`) | (2) — `run_kind: worktree`, non-reproducible. |

---

## Goal: `candidate-contamination`

Primary metric: `contaminated_frac` (minimize) · Target: 0.02

### Current leader

None. No result has met the target.

The best measured value is `contaminated_frac = 0.058` (`r-0013`,
`temporal-adjacency-exclusion` at a 60 s window), which fails condition (1) — it is far from target —
and came with a recall cost of 0.028 that the goal's success criterion treats as disqualifying.
Recorded here because "nothing qualifies yet" is a state worth being able to read off the board,
rather than an empty section.

### Superseded

| Date | Experiment | Metric | Run | Superseded by |
|---|---|---|---|---|
| — | — | — | — | — |
