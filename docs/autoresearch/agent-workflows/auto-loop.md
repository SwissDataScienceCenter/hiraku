# Skill reference: bounded autonomous loop over a goal

Loaded to run a bounded autonomous loop over a goal's tree. Requires explicit approval of the goal's `research/config.md` bounds and the
node's `policy.md` in the active conversation before the first iteration. Approval is never inferred
from a file's presence.

## Before starting, state back and get agreement on

The goal and its `primary_metric`, `direction`, `target`; the evaluation protocol; the run budget,
depth limit, and concurrency limit; the permitted commands; the stopping conditions; and what will
end the loop and ask for a human. If any is missing from `research/config.md` or `policy.md`, ask
rather than choosing a default.

## Each iteration

1. **Read the tree.** Every node under the goal, their statuses and verdicts, the run table, open
   proposals. Never rely on memory of an earlier iteration; the files are the state.
2. **Pick the next node.** Prefer, in order: an open node with a launch ready; the highest-value
   approved proposal; a diagnostic on the most recently concluded inconclusive node. If nothing
   qualifies, the goal is exhausted — go to *Ending*.
3. **Launch** a named entrypoint via `--commit`, so the run is reproducible and eligible for
   promotion. A working-tree launch is for debugging only and can never be promoted.
4. **Record** findings against the run rows. Metrics are read from the table, never restated from
   the process output.
5. **Decide**: expectation met → conclude `supports`, then the promotion gate. Not met → triage per
   [triage-failure.md](triage-failure.md).
6. **Check bounds** before continuing. Runs used, depth reached, wall-clock, cost, concurrent
   worktrees.

## Hard pauses

Stop and ask, mid-loop, for any of these. This list is the loop's boundary, and it is not
negotiable by the loop:

- Any bound in `research/config.md` or `policy.md` would be exceeded.
- The goal's scope or evaluation protocol would change.
- A new dataset, dependency, or credential is needed.
- The mode or a policy would change, or a child would be given wider authority than its parent.
- A protected branch would be merged.
- A promotion would happen with no independent reproduction.
- The same class of failure appears three times in a row — that is a signal about the setup, not a
  reason for a fourth attempt.
- A literature-derived proposal is the best next step.

## What the loop may and may not judge

It may judge **execution**: the process exited zero, the row was written, four seeds ran, the
lockfile hash matches. These are checkable facts, and self-checking them is what makes autonomy
useful.

It may not judge **acceptance**: this is better, this settles the question, this belongs on the
leaderboard. Acceptance needs something outside the loop — the researcher, or at minimum the
independent reproduction the promotion gate requires. A loop that both produces a result and
declares it good has verified nothing.

## Ending

End when the target is met, the budget is spent, or the goal is exhausted — every open node
concluded and no proposal passing the investigation test. Exhaustion is a real, valuable outcome:
say so plainly rather than manufacturing another node to avoid it.

Then write a summary for review, from the files rather than from memory: what was asked, the tree
with each node's verdict, what is now known, what was ruled out and how, what remains open and why
it could not be settled, every promotion with its evidence, and every bound that was hit. Point at
the branch and the dashboard, and leave the merge decision to the researcher.
