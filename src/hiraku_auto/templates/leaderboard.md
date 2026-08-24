# Leaderboard

Promoted results only — the current best answer to each goal, with its evidence. This is not a
mirror of the run table; `hiraku runs --sort` already ranks every run. This file answers a different
question: **which results do we stand behind?**

Append-only. A superseded leader moves to its goal's history section; entries are never edited.

A result reaches this file only if all five hold:

1. Better than the incumbent on the goal's `primary_metric`, in its `direction`.
2. `run_kind: commit` — a working-tree run may never be promoted, however good the number.
3. Produced under the goal's evaluation protocol, unmodified.
4. Independently reproduced at a different seed, agreeing within the goal's `agreement_tolerance`.
5. Signed off — by the researcher in `guided`; in `auto`, by the reproduction in (4) plus a recorded
   entry a human can audit.

---

## Goal: `<goal-id>`

Primary metric: `<metric>` (`maximize` | `minimize`) · Target: `<value>`

### Current leader

| Field | Value |
|---|---|
| Experiment | `<experiment-id>` |
| Metric | `<metric> = <value>` |
| Run | `<run-id>` |
| Commit | `<sha>` |
| Reproduction | `<run-id>` at seed `<n>`, `<metric> = <value>` |
| Promoted | `<YYYY-MM-DD>` |
| Signed off by | `<name>` or `auto + reproduction` |

**Why it holds.** One paragraph: the mechanism, and what the comparison against the incumbent
actually establishes.

**What it does not establish.** The limits — conditions not tested, subsets where it does not hold,
and what would overturn it.

### Superseded

| Date | Experiment | Metric | Run | Superseded by |
|---|---|---|---|---|

### Not promoted, worth knowing

Results that failed the gate but should not be lost — a strong single-seed number awaiting
reproduction, or an improvement produced under a modified protocol. State which condition failed.
