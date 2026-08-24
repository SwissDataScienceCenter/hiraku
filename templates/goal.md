---
id: <goal-id>
status: active            # active | achieved | abandoned | paused
primary_metric: <metric>
direction: maximize       # maximize | minimize
target:                   # optional: the value that would settle the question
---

# <Goal title>

## Question

The open research question, in one or two sentences. Falsifiable, and about the world rather than
about the code.

## Why it matters

What decision or downstream use depends on the answer. This is what justifies the run budget.

## Success criterion

What result would count as answering it — `primary_metric` reaching `target`, or a stated comparison
holding. If there is no number that would settle it, say what evidence would instead.

## Evaluation protocol

Fixed **before** any run, and inherited unchanged by every experiment under this goal. An experiment
needing a different protocol is answering a different question and belongs under a different goal.

- Dataset and version, with checksum
- Splits, and how they were produced
- Metrics, with their exact definitions
- Seeds and how many
- What is held fixed across all comparisons

Changing anything here invalidates every comparison already made under this goal. If it must change,
that is a researcher's decision, and the goal's history section records what became incomparable.

## Out of scope

Questions deliberately not asked here, so a loop does not drift into them.

## Open threads

Known unknowns that are not yet experiments. A parking area — not a queue, and not a commitment.

## History

Protocol changes, status changes, and superseded promoted results, newest first, each with a date and
a reason.
