# The loop: what happens when a run lands

The interesting part of an autoresearch tool is not launching runs. It is deciding what a
disappointing result means, and whether the next question is worth asking. Left implicit, an agent
will always find one more thing to try, and the tree grows without converging. This document makes
that decision procedure explicit enough to be audited afterwards.

```
run lands
   │
   ├── does the result meet the node's expectation?
   │        yes → conclude verdict: supports → promotion gate → leaderboard.md
   │        no  → triage
   │
   └── triage: classify, then apply the investigation test
            ├── passes → propose a child (guided: ask; auto: create within budget)
            └── fails  → conclude terminally, record why it is terminal
```

## Step 1 — Classify the failure

Three classes, and the class determines what may happen next. The agent must name the class and its
evidence in the node's **Findings**.

**A. Execution defect.** The run did not test the expectation: a crash, a shape mismatch, a leak
between splits, a metric computed on the wrong subset, an entrypoint that silently used stale
data. The expectation is untested and the result carries no information.
→ Fix and re-run on the same node. This is not a new node; nothing was learned about the
hypothesis, so nothing should be recorded as if it were. Record the defect and the fix in
**Findings** so the run rows that preceded it are interpretable.

**B. Expectation refuted.** The implementation is sound, the protocol was followed, the predicted
effect did not appear.
→ `verdict: refutes`. This is a **result**, not a problem to be solved. The default action is to
conclude the node and stop. A child is justified only if triage produces a *specific named cause*
worth separating — see step 2.

**C. Inconclusive.** The evidence cannot separate "the expectation holds" from "it does not":
variance across seeds swamps the effect, the comparison is confounded, the evaluation set is too
small, a dependency changed mid-experiment.
→ `verdict: inconclusive`. A child that removes the specific ambiguity is usually well justified,
and is usually `kind: diagnostic`.

The distinction between B and C is where honesty is won or lost. An agent that reads every negative
result as "inconclusive, needs more investigation" produces an infinitely deep tree and never
records a finding. The rule: **inconclusive requires naming the specific competing explanation the
evidence cannot rule out.** Absent that, a negative result is a refutation.

## Step 2 — The investigation test

A child may be proposed only if **every** condition holds. The agent records the answers in the
proposal; a reviewer can then check the reasoning rather than re-derive it.

1. **Named cause.** There is a specific, stated mechanism suspected of producing the result — not
   "tune the hyperparameters further", not "try a larger model". "Recall drops only on
   night-time sequences, so the failure may be illumination rather than viewpoint" qualifies.
2. **Discriminating.** The proposed run distinguishes between at least two explanations. Write down
   both, and what each predicts. A run whose every possible outcome leads to the same next action
   is not worth doing.
3. **Novel.** No existing node in the goal's tree — including `abandoned` ones — already tested
   this. The tree is the memory; failed and abandoned nodes are kept precisely so the loop cannot
   rediscover them. Checking this before proposing is mandatory.
4. **In scope.** The question falls inside the goal's stated scope and its evaluation protocol
   applies unchanged. If answering it requires a different protocol, it is a different goal, and
   that is a decision for the researcher.
5. **Within budget.** The goal's remaining run budget and depth limit (`research/config.md`) admit
   it.
6. **Proportionate.** The expected information is worth the cost. A four-hour run to remove a
   confound that the evidence already makes unlikely is not.

Failing any condition is not an error — it is the loop's stopping signal. Record the node as
concluded, state which condition failed, and stop. That sentence is the most valuable line in the
record: it is the difference between "we stopped" and "we stopped because the remaining
explanations were untestable within this goal's protocol".

## Step 3 — Who decides

| Node's `mode` | On a passing investigation test |
|---|---|
| `guided` | Write `research/proposals/<id>.md` and ask. Nothing is created until the researcher decides. |
| `auto` | Create the child directly, within the policy's run budget and depth limit, and record it. Pause and ask if any bound would be exceeded, if scope would change, or if the protocol would change. |

The existing rule that a program cannot grant itself authority its policy withheld extends to the
tree: a child inherits its parent's mode and may narrow it, never widen it. A guided node's child is
guided.

Both modes write the same files. The only difference is whether a human answers before or after.

## Recording a terminal failure

An abandoned or refuted node is a first-class record, not an absence. Its **Conclusions** must
carry:

- the verdict and the class from step 1;
- the run IDs that support it;
- which investigation-test condition failed, when a child was considered and rejected;
- what would change the answer — the observation or resource that would make this worth reopening.

That last line is what makes the tree useful six months later, and what stops the same idea being
proposed again by a fresh agent with no memory of the conversation.

## The promotion gate

`research/leaderboard.md` holds promoted results only — the current best answer to each goal,
with the evidence. It is not the run table and must not become a mirror of it. `hiraku runs --sort`
already ranks every run; the leaderboard answers a different question: *which results do we
stand behind?*

A result is promoted only if all of these hold:

1. **Better on the goal's terms** — an improvement on `primary_metric` in `direction`, against the
   current leader.
2. **Reproducible** — `run_kind: commit`, so the run executed a committed snapshot.
   A working-tree run may never be promoted, however good the number.
3. **Protocol-conformant** — the goal's evaluation protocol, unmodified. A result produced under a
   changed protocol is not comparable to the incumbent, and swapping the protocol to win is the
   most common way a leaderboard becomes meaningless.
4. **Independently reproduced** — at least one additional run at a different seed, agreeing within
   the goal's stated tolerance. A single-seed improvement is a candidate, not a result.
5. **Signed off** — in `guided`, by the researcher. In `auto`, by the independent reproduction in
   (4) plus a recorded promotion entry that a human can audit later.

Condition 5 borrows a distinction worth naming, from the cross-model review literature: a loop may
judge *execution* facts on its own — the process exited zero, the row was written, four seeds ran —
but not *acceptance* facts — this is better, this settles the question. Acceptance needs something
outside the loop: a human, or at minimum evidence the loop did not choose for itself. The gate above
is that separation written as file conventions rather than as a second model, which suits a tool
whose review surface is already a pull request.

Every promotion appends an entry: goal, experiment, run ID, commit, metric value, the reproduction
run, the date, and who or what signed it off. Entries are never edited — a superseded leader moves
to the goal's history section. The board is append-only for the same reason the run table is: a
leaderboard you can quietly rewrite is not evidence.

## Stopping the goal

A goal stops when one of these is true, and the reason is recorded in the goal record:

- **Achieved** — a promoted result meets `target`.
- **Exhausted** — every open node is concluded and no proposal passes the investigation test.
- **Out of budget** — the run or time budget in `research/config.md` is spent.
- **Superseded** — the question changed. This is always a researcher's decision, never the loop's.

"Exhausted" is the outcome the investigation test is designed to make reachable. Without it a
research loop has no natural end, and the honest report — *we asked everything we could
discriminate, and here is what we know* — never gets written.
