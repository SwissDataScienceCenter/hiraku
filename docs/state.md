
# Hiraku-auto: experiment state model

## Two layers

Every call answers one question — "what should happen next for this goal?" —
but that question is really two separate ones stacked on top of each other:

1. **Node state** — where is *one* experiment in its own lifecycle right now?
2. **Scheduling policy** — given a whole tree of experiments, each possibly in a *different* state
   at the same time, which one gets attention first?

These are different kinds of thing. Node state is a property of a single record. Scheduling
policy is a tie-breaking rule over the whole tree. Keeping them distinct matters because they
change for different reasons: node state changes when the data model changes, scheduling policy
changes when the research process's priorities change.

## 1. Node state — the linear lifecycle

An experiment's persisted `status` field has five possible values: `proposed`, `approved`,
`running`, `concluded`, `abandoned`. On its own this is too coarse to schedule against, so the
tool derives a finer set of operating states from `status` combined with `verdict`, `runs`, and
whether `launch.yml` still holds its unfilled stub:

| State | Derived from | Meaning |
| --- | --- | --- |
| `proposed` | `status == proposed` | Exists, but nobody has decided whether to pursue it. |
| `approved, not implemented` | `status == approved`, no runs, `launch.yml` still the stub | Direction accepted; no code written yet. |
| `approved, implemented` | `status == approved`, no runs, `launch.yml` has a real command | Ready to run. |
| `running` | `status == running` | A run is in flight. |
| `awaiting analysis` | has runs, no `verdict`, `status != concluded` | Results exist; nobody has judged them yet. |
| `concluded, untriaged` | `verdict` in `{refutes, inconclusive}`, no children | The result needs a decision about what happens next. |
| `concluded, settled` | `verdict` set and either `supports`, or already has children | This branch of reasoning is done. |
| `abandoned` | `status == abandoned` | Explicitly dropped. |

For a single node, these states form a mostly linear path:

```
proposed → approved (not implemented) → approved (implemented) → running
    → awaiting analysis → concluded, untriaged → concluded, settled
```

`abandoned` can be reached from most points on this path — it is an exit, not a stage. `concluded,
untriaged` is the one branch point: triage can spawn zero, one, or several new child nodes, each of
which starts this same path over again, one level deeper in the tree.

`discover`, `implement`, and `commit` do not appear in this table because none of them change
`status`. `discover` writes a separate record type (a proposal) that only becomes a node if
accepted. `implement` and `commit` change what evidence exists on disk — real code, a real
`launch.yml`, a bound git commit — without touching `status` at all. Whether something has been
implemented is answered by checking that evidence directly, not by a stored state, because a
stored value could go stale relative to the file it is supposed to describe.

## 2. Linear per node, parallel across the tree

The lifecycle above describes one node walking a line. A goal is not one node — it is a tree of
them, each independently at its own point on that line at the same time. At any moment the tree
might look like:

```
baseline        ─ concluded, settled
├─ variant-a    ─ running
├─ variant-b    ─ approved, not implemented
└─ variant-c    ─ concluded, untriaged
```

Four nodes, four different states, all true simultaneously. `suggest_next()` can only return one
action per call, so something has to decide which of these four gets attention. That is what the
scheduling policy is for — it is not about any one node's state, it is about arbitrating across
all of them at once.

## 3. The loop: how a branch re-enters the cycle

This is the part that makes it a *loop* rather than a one-shot pipeline: a concluded branch that
did not settle the question does not end the tree, it starts a new one, one level down.

```
proposed → approve → implement (if needed) → launch → running
    → analyze (writes verdict + concluded, in the same step)
        → verdict: supports              → concluded, settled — done, nothing schedules here again
        → verdict: refutes/inconclusive  → concluded, untriaged
                                              → triage
                                                  → new child, status: proposed → back to the top
```

Two precisions worth being exact about, since it is easy to describe this loop more loosely than
the code actually behaves:

- **`triage`'s output is a new EXPERIMENT with `status: proposed`**— it only needs `approve`, the same gate
  every other proposed node needs, not an extra review step on top of it.
- **The loop only continues where the result did not settle the question.** `supports` ends that
  branch — `concluded, settled`, nothing schedules against it again. Only `refutes`/`inconclusive`
  re-enters the cycle, and only until `triage` has produced children for it; `concluded, untriaged`
  is the one state on the whole path that is still "open."

Each child a `triage` call produces restarts this exact path independently, one level deeper — this
is the same "parallel across the tree" picture from the section above: at any moment, different
branches of the same goal can be sitting at different points along this same loop.

## 4. Goal-level scheduling policy

The policy is a fixed priority order: scan the tree once per check, in this order, and act on the
first match.

| Priority | Condition (first match across the tree wins) | Action | Stops for a human? |
| --- | --- | --- | --- |
| — | The goal has zero experiments | `design` or `discover` (researcher picks) | Yes — always. |
| 0 | Any node `running` | `analyze` | No. |
| 1 | Any node `approved`, no runs, not implemented | `implement` | Yes — always. |
| 1 | Any node `approved`, no runs, implemented | `launch` | Yes, unless `--launch` is passed. |
| 2 | Any node has runs, no verdict | `analyze` | No. |
| 3 | Any node concluded `refutes`/`inconclusive`, no children | `triage` | Yes, if triage finds a blocking condition. |
| 4 | Any node `proposed` | `approve` | Yes — always. |
| — | None of the above | `exhausted` | — (nothing left to do). |

Two things about this table are worth being explicit about:

- **"Priority" only matters because more than one condition can be true at once.** If only one
  node existed at a time, there would be nothing to arbitrate — the order would not matter. It
  matters here because a real tree usually has several nodes in several states simultaneously, and
  the order decides which one wins.
- **The empty-tree check sits outside the per-node scan.** It fires only when there is nothing to
  iterate over at all — it is a bootstrap condition, not one more priority level in the same list
  as the others. It also does not resolve to a single action: the tool hands the researcher two
  options at that point, `design` (formalize an idea, yours or the tree's own next step) and
  `discover` (search for one first) — the choice between them is a human one, not something the
  policy arbitrates. Whether that is the right way to model this point is one of the open questions
  below.

## 5. Summary

- Node state is linear and per-record; scheduling priority is an arbitration rule over many nodes'
  states at once. They answer different questions and should not be collapsed into one table.
- The tool tracks five persisted statuses; the finer states used for scheduling are computed on
  read, not stored, specifically so they cannot drift out of sync with the evidence they describe.
- `discover`, `implement`, and `commit` are workflows whose effects live in separate records or
  files, not in `status` — this is deliberate, not a gap in the status enum.
- Every human-gated point in the table above is a place the researcher's judgment is required by
  design: what to build (`design`/`discover`), what counts as evidence (`approve`), and what a
  result means (`triage`'s blocking condition).
- The tree is not a one-pass pipeline: `triage` closing on `refutes`/`inconclusive` is what turns a
  dead end into a new child to run, which is what makes this a *loop* rather than a sequence that
  terminates the first time a hypothesis fails.

## Open questions

- **Should "implemented" become a stored field?** It is currently answered by reading
  `launch.yml` and checking whether it still holds the unfilled stub. That avoids a second source
  of truth, but means the answer costs a file read rather than a field lookup — fine at today's
  scale, worth revisiting if the tree grows large enough that repeated scans matter.
- **Is the priority order itself right?** "Attend to running work before starting new launches"
  and "get new launches started before finishing analysis of older results" are both defensible,
  but neither was chosen deliberately — it is the order the checks happen to run in. Whether
  research should actually be paced this way (versus, say, finishing analysis before starting new
  runs) has not been discussed on its own terms.
- **Does `design` belong in the same priority list as the rest, or is it categorically different?**
  Right now it can only fire when the tree is completely empty, so a goal that already has some
  structure can never be offered "propose more nodes" as a scheduled action — only as something a
  person invokes directly. Is that the intended boundary between design and the rest of the loop?
- **Do `analyze` and `triage` need to exclude `abandoned` nodes explicitly?** Both conditions check
  `runs`/`verdict`/`children` but not `status`. A node someone has explicitly abandoned, if it still
  has unanalyzed runs or a stale untriaged verdict from before it was abandoned, would still match
  those conditions today. Whether that is desired or an oversight has not been decided.
- **Should the priority table itself become declarative data** (a list the tool reads) **rather
  than the order of `if` statements in code?** That would make the policy inspectable and editable
  without a code change, at the cost of an extra layer of indirection for what is currently a short,
  readable function.
- **What does priority mean once more than one agent can act at once?** `suggest_next()` returns
  exactly one `(node, action)` pair per call, which is why priority currently reads as "pick the
  single next thing to do." `launch` already does not work this way — it batches every eligible
  node into one parallel run, up to `--max-parallel`, and the run table is already safe for
  concurrent writes. `implement`, `analyze`, and `triage` have no equivalent: today they can only
  ever surface one node at a time, not because those actions cannot run concurrently, but because
  nothing yet asks "give me every eligible pair," only "give me the highest-priority one." If that
  changed, priority would stop meaning "what to do next" and start meaning "which candidates get a
  slot first when eligible work outnumbers available agents or capacity" — the same ordering, doing
  a different job. Approval gates are the one place this does not apply at all: no amount of
  parallel execution reduces a human decision to something concurrency can absorb.
