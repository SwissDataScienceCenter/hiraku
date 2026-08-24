---
id: {{id}}
status: active
primary_metric: {{primary_metric}}
direction: {{direction}}
target: {{target}}
gate_metric: {{gate_metric}}
---

# {{title}}

## Question

{{question}}

## Why it matters

What decision or downstream use depends on the answer. This is what justifies the run budget.

## Success criterion

What result would count as answering it — `{{primary_metric}}` reaching `target`, or a stated
comparison holding. If there is a gate metric, a result that improves the primary metric while
degrading the gate does not count.

## Evaluation protocol

Fixed **before** any run, and inherited unchanged by every experiment under this goal. An experiment
needing a different protocol is answering a different question and belongs under a different goal.

- Dataset and version, with checksum
- Splits, and how they were produced
- Metrics, with their exact definitions
- Seeds and how many
- What is held fixed across all comparisons

Changing anything here invalidates every comparison already made under this goal.

## Out of scope

Questions deliberately not asked here, so a loop does not drift into them.

## Open threads

Known unknowns that are not yet experiments. A parking area, not a queue.

## History

- {{created}} — goal opened.
