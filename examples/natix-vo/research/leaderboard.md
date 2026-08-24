# Leaderboard

Promoted results only — the current best answer to each goal, with its evidence. Not a mirror of the
run table; `hiraku runs --sort` already ranks every run. This answers a different question: **which
results do we stand behind?**

Append-only. A superseded leader moves to its goal's Superseded table. Entries are never edited.

A result reaches this file only if all five hold:

1. Better than the incumbent on the goal's primary metric, in its direction, **and** passing the
   goal's gate metric (`completion_rate` / `align_success_rate`).
2. `run_kind: commit` — a working-tree run may never be promoted, however good the number.
3. Produced under the goal's evaluation protocol, unmodified.
4. Independently reproduced at a different seed, agreeing within `agreement_tolerance`.
5. Signed off — researcher in `guided`; in `auto`, the reproduction plus a recorded entry a human can
   audit.

---

## Goal: `vo-backend-selection`

Primary metric: `ate_rmse_m` (minimize) · Gate: `completion_rate` · Target: **[to set]**

### Current leader

None — no run has been recorded through Hiraku yet.

`occany-baseline` is the intended reference but its Findings are still a gap, and results produced
outside Hiraku are not promotable under condition 2. They can serve as the comparison reference
without being promoted; record them in the baseline's Findings and mark them non-reproducible.

### Superseded

| Date | Experiment | Metric | Run | Superseded by |
|---|---|---|---|---|
| — | — | — | — | — |

### Not promoted, worth knowing

| Experiment | Result | Failed condition |
|---|---|---|
| `rig-camera-vo` | No improvement over single-camera | (1) — not better on the goal's terms. A recorded negative result, and the reason two diagnostics exist. |

---

## Goal: `cross-domain-features`

Primary metric: `align_median_err_m` (minimize) · Gate: `align_success_rate` · Target: **[to set]**

### Current leader

None — no run recorded.

### Superseded

| Date | Experiment | Metric | Run | Superseded by |
|---|---|---|---|---|
| — | — | — | — | — |
