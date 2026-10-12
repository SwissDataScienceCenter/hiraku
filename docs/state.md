# Hiraku-auto: experiment state model

## Overview: three questions, kept separate

Every scheduling decision the tool makes answers three different questions, kept apart on purpose
because they change for different reasons:

1. **Node state** — where is *one* experiment in its own lifecycle right now? A fact about that
   experiment's evidence: does code exist, has it run, what did the result mean.
2. **Gates** — for a given step, does moving forward need a human decision, or can it be satisfied
   automatically? A property of the *step*, controlled by the node's `mode` (`guided` or `auto`),
   independent of which state the node is in.
3. **Scheduling policy** — given a whole tree of experiments, each possibly in a different state at
   the same time, which one gets attention first?

State changes when the data model changes. Gates change when the process's tolerance for
unattended operation changes. Scheduling policy changes when research priorities change. Collapsing
any two of these into one concept makes all three harder to reason about — in particular, approval
belongs entirely to the gates axis, not to node state, and this document keeps it there.

## 1. Node state — the linear lifecycle

An experiment moves through a small number of states, each one a fact about what evidence exists
for it — not a decision about it:

| State | What makes it true | Meaning |
| --- | --- | --- |
| `proposed` | exists, no real launch configuration yet | An idea worth considering; nothing has been built for it. |
| `implemented` | a real, runnable launch configuration exists, no runs yet | Code and launch config exist; nothing has been executed yet. |
| `running` | a run attributed to it reports it is still running (read from the tracking backend), or a live `mark-running` marker | Evidence is being generated right now. Takes precedence over `awaiting analysis` and over an earlier verdict, because results so far are partial. |
| `awaiting analysis` | has runs, no verdict yet | Results exist; nobody (or nothing) has judged them yet. |
| `concluded` | has a verdict (`supports`, `refutes`, or `inconclusive`), no children, not yet triaged | The result needs a decision about whether it's worth going further. |
| `completed` | has children, or `triage` already decided not to produce any, or explicitly dropped | This branch of reasoning is done — nothing schedules here again. |
| `abandoned` | explicitly dropped | Not a state of its own any more — a fact (`Node.abandoned`) that resolves straight to `completed`, the same way a `triage` call that decided "no further children" does. Kept here as a name because it's still how you *reach* that fact, even though `node_state` returns `completed` for it. |

`running` is the one state not read off the node's own record, and the only one that comes from outside it.
`hiraku launch` returns when its dispatcher does, so a long experiment leaves nothing in the record while
it runs. Two signals fill that gap, and either one is enough:

- **The run reports it** (no setup). Every tracking backend already carries the run's own status: MLflow
  `RUNNING`, Weights & Biases `running`, or a `status` column in a CSV source. It works for any way of
  launching (a laptop, Slurm, a cloud job), provided the experiment registers its run when it *starts*.
  A run that has said "running" for more than a week is ignored, because a killed process can leave it
  stuck.
- **A marker you set** when the run cannot report itself: `hiraku-agent mark-running ID` with `--job-id`
  (Slurm), `--pid` (a local process) or `--check CMD` (any command that exits 0 while the work is alive).
  It is never cleaned up by hand: it counts for as long as its check holds, so a finished or cancelled job
  stops counting by itself.

If the work cannot be checked (`squeue` unreachable, say), it counts as still running rather than
finished. The scheduler treats a `running` node as neither implementable, launchable, analysable nor
triageable, and answers `wait` (not `exhausted`) when only running nodes remain.

For a single node, the non-exit states form a linear path:

```
proposed → implemented → running → awaiting analysis → concluded → completed
```

**A `supports` verdict is `concluded`, not automatically `completed`.** The earlier version of
this model let a `supports` verdict settle a branch by itself, on the theory that a hypothesis
that held needed no further attention. That is not generally true: a result that supports its
expectation can still be worth a robustness check across seeds, a natural ablation isolating
which part of the change mattered, or simply pushing the same lever further — exactly the same
shape of question a `refutes`/`inconclusive` result asks. So both kinds of verdict land on
`concluded` and go through the same decision, `triage`, which judges them through different
lenses (see `prompts/triage.md`) but asks the same question either way: is there a good reason to
go deeper. `concluded` is the one branch point on this path: `triage` can produce zero, one, or
several new child nodes, each of which restarts this same path independently, one level deeper in
the tree — or it can produce none and still move the node to `completed`, because the record that
a decision was made (`Node.triaged`) is what distinguishes "nobody has looked at this yet" from
"somebody looked, and decided to stop here."

`approved` is deliberately not on this list. Whether a node may proceed is a decision, not a fact
about its evidence — see the gates section below. Two experiments can be in the identical state
(say, both `implemented`) and still differ in whether a human has cleared them to move forward; that
difference does not belong in the state model.

## 2. Gates: optional by `mode`, not a state

A gate is a point where moving a node forward could require a human — or not, depending on the
node's `mode` (`guided` or `auto`). A gate is not a place on the lifecycle path above; it is a
condition checked before acting on a node that is already in some state.

- **Approval.** Before anything moves a `proposed` node forward — writing code for it or launching
  it — it checks whether the node has been approved. In `guided` mode this needs an explicit
  decision from a person: approval is a judgment about research direction, not a fact that can be
  derived from the node's own evidence, so nothing can substitute for it. In `auto` mode this clears
  by itself, and the node proceeds straight into implementation and launching with no stop. Approval
  is checked once, on entry — it is not re-cleared separately before implementing and again before
  launching.
- **Implement, trigger.** Code has to exist before a run is possible, in either mode — that part is
  not optional, and the agent is what writes it either way (`workflows.run_implement`), never a
  person. What `mode` changes is only who *triggers* that call: in `guided` mode, a person runs
  `hiraku-agent implement` (or `/hiraku "implement ..."` from inside a coding agent) once the node
  is approved; in `auto` mode, `hiraku-agent loop` triggers it itself, with no stop. Conflating "who
  writes the code" with "who triggers writing it" was an earlier mistake in this project's own
  thinking about this gate — they are the trigger axis and the authorship axis, and only the first
  one is ever optional.
- **Launch, trigger.** Same shape as implement — a run has to be launched by something. In `guided`
  mode this stops for a person; in `auto` mode it launches on its own.
- **Triage's blocking condition.** When triage finds something that needs judgment, that stop is not
  yet mode-dependent — it always requires a person today. Whether it should follow the same pattern
  as approval is an open question below.

The distinction that matters: `implemented` describes a fact (code exists), while approval describes
a decision about that fact (should this node proceed) — and only the second kind of thing is ever
optional. A gate does not add a state; it decides how a node is allowed to move from one state to
the next.

## 3. Linear per node, parallel across the tree

The lifecycle above describes one node walking a line. A goal is not one node — it is a tree of
them, each independently at its own point on that line, in its own `mode`, at the same time. At any
moment the tree might look like:

```
baseline        ─ completed
├─ variant-a    ─ running
├─ variant-b    ─ implemented, awaiting the approval gate
└─ variant-c    ─ concluded
```

Four nodes, four different states, all true simultaneously — and each may or may not need a human to
move it forward, independent of the others. Something has to decide which of these gets attention
first when more than one is ready to move. That is what the scheduling policy is for.

## 4. The loop: how a branch re-enters the cycle

This is the part that makes it a *loop* rather than a one-shot pipeline: a concluded branch that did
not settle the question does not end the tree, it starts a new one, one level down.

```
proposed → (approval gate) → implement (agent writes it) → launch → running
    → analyze (writes verdict + concluded, in the same step)
        → triage (same decision, either verdict — different lens per `prompts/triage.md`)
            → proceed: yes  → new child(ren), proposed → back to the top
            → proceed: no   → completed — done, nothing schedules here again
```

Three precisions worth being exact about:

- **Analyzing and concluding are the same step, not two.** Writing the verdict and marking the node
  concluded happen together — there is no separate "conclude" action in the scheduling policy. What
  triage reacts to is the verdict analyze already wrote.
- **Triage's output is a new node, `proposed`, not a separate kind of record.** It passes through the
  same approval gate as anything else that starts at `proposed` — triage grants it no shortcut.
- **Every verdict goes through triage, not only a miss.** The earlier version of this loop let
  `supports` skip straight to done; it does not any more (see section 1's note on why). What ends a
  branch is `triage` actually deciding to stop — recorded as `Node.triaged` — not which way the
  verdict happened to go. `concluded` is the one state on the whole path that is still "open."

In `auto` mode, this entire loop can run with no stop at all, from `proposed` all the way back
around to the next `proposed` — every gate on the path clears itself, including `implement`'s (the
agent writes the code regardless of `mode`; only whether a person has to trigger it changes). What
still ends a branch is unchanged in shape, just no longer tied to the verdict's polarity: `triage`
deciding not to proceed, or `abandoned`.

Each child a `triage` call produces restarts this exact path independently, one level deeper — the
same "parallel across the tree" picture from section 3 applies to it immediately.

## 5. Goal-level scheduling policy

The policy is a fixed priority order: scan the tree once per check, in this order, and act on the
first match.

| Priority | Condition (first match across the tree wins) | Action | Gate before it proceeds |
| --- | --- | --- | --- |
| — | The goal has zero experiments | `design` or `discover` (researcher picks) | Always a researcher choice, regardless of `mode` |
| 1 | Any node `proposed`, approval cleared, no real code yet | `implement` (agent writes it) | Approval (once, on entry) |
| 1 | Any node `implemented`, approval cleared, no runs | `launch` | Approval (already cleared above) |
| 2 | Any node has runs, no verdict, and runs the last analysis has not already judged | `analyze` | None |
| 3 | Any node `concluded` (any verdict, no children, not yet triaged) | `triage` | Not yet mode-dependent — always stops on a blocking condition |
| 4 | Any node whose runs `triage` classed as an execution defect (class A), and no other runs yet | `fix` (a person fixes the code, binds the new commit, launches again) | — |
| 4 | Any node whose last `analyze` set no verdict, with no new runs since (`analyzed_runs`) | `needs_evidence` (what is missing is in its `## Missing evidence`) | — |
| 4 | Any node `implemented` but not approved (an `update` cleared the approval after the code was bound) | `approve`, after checking the code still matches the expectation | This *is* the gate |
| 5 | Any node `proposed`, approval not yet cleared | `approve` (or auto-clears) | This *is* the gate |
| — | None of the above, but a node is `running` | `wait` | None |
| — | None of the above | `exhausted` | — |

Two things worth being explicit about:

- **"Priority" only matters because more than one condition can be true at once.** If only one node
  existed at a time, there would be nothing to arbitrate — the order would not matter. It matters
  here because a real tree usually has several nodes in several states simultaneously, and the order
  decides which one wins.
- **The empty-tree check sits outside the per-node scan.** It fires only when there is nothing to
  iterate over at all — a bootstrap condition, not one more priority level in the same list as the
  others. It also does not resolve to a single action: the tool hands the researcher two options,
  `design` (formalize an idea) and `discover` (search for one first) — a human choice in either
  `mode`, since nothing yet exists for `auto` mode to act on.

`fix` and `needs_evidence` exist so that a call that cannot change anything is not repeated.
Analysing the same runs again cannot turn a `null` verdict into a real one, and runs that did not
test the expectation stay void however often they are read. Both clear by themselves when new runs
arrive. `hiraku-agent loop` stops on them, as on any action that needs a person, and additionally
stops when it is about to repeat the previous step on the same node with nothing changed.

## 6. Summary

- Node state is a fact about a single record's evidence; the approval gate is a decision about
  whether to act on that fact; scheduling priority is an arbitration rule over many nodes at once —
  three different questions, three different reasons to change, kept in three different sections.
- `implemented` sits in the lifecycle because it is a fact — real code exists or it doesn't.
  `approved` does not sit in the lifecycle, because it is a decision, and decisions are exactly the
  thing `mode` is allowed to make optional.
- Every gate that is optional today — approval, and the implement/launch triggers — resolves the
  same way in `auto` mode: no stop, because either nothing but a person's judgment could satisfy it
  (approval, cleared automatically) or the tool can perform the step itself (`implement`, `launch`
  — `implement` meaning the agent runs on its own, not that a person's code gets skipped, since the
  agent was always who wrote it).
- The tree is not a one-pass pipeline: `triage` deciding to proceed is what turns any concluded
  node — a `refutes`/`inconclusive` dead end or a `supports` worth pushing further — into a new
  child to run, which is what makes this a *loop* rather than a sequence that terminates the first
  time a question gets an answer.
- In `auto` mode, the entire loop — proposed through concluded and back around through triage — can
  run with no human stop at all; in `guided` mode, every gate above stops as documented.

## Open questions

- **Resolved — how does a cleared approval get recorded?** An explicit field pair,
  `Node.approved_by`/`approved_at` (`gates.approve`), not a derived fact — `hiraku-agent approve`
  writes them, `gates.is_approved` reads them back (`True` unconditionally in `auto` mode).
- **Does triage's blocking condition become an optional, `mode`-dependent gate, the same as
  approval?** Right now it always stops. If the condition it checks is itself a derivable fact
  rather than a judgment call, `auto` mode could clear it the same way it clears approval.
- **Does `design` belong in the same priority list as the rest, or is it categorically different?**
  It can only fire when the tree is completely empty, so a goal that already has some structure can
  never be offered "propose more nodes" as a scheduled action — only as something invoked directly.
- **Do `analyze` and `triage` need to exclude `completed` nodes explicitly?** Neither condition
  currently checks status. A node someone has explicitly abandoned, if it still has unanalyzed runs
  or a stale, not-yet-triaged verdict from before it was abandoned, would still match those
  conditions.
- **Should the priority table become declarative data** (a list read at runtime) **rather than the
  order of checks in code?** That would make the policy inspectable and editable without a code
  change, at the cost of an extra layer of indirection for what is currently a short, readable
  function.
- **What does priority mean once more than one agent can act at once?** Scheduling currently returns
  one `(node, action)` pair at a time, which is why priority reads as "pick the single next thing to
  do." Launching already works differently — it batches every eligible node into one parallel run.
  Implementing, analyzing, and triaging have no equivalent yet. If they gained one, priority would
  stop meaning "what to do next" and start meaning "which candidates get a slot first when eligible
  work outnumbers capacity" — the same ordering, doing a different job. In `guided` mode, no amount
  of parallelism reduces a human decision to something concurrency can absorb; in `auto` mode this
  constraint mostly disappears, since the gates it used to protect are no longer waiting on a person.
