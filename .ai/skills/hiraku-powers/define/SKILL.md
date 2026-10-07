---
name: hiraku-powers-define
description: Use when starting a new Hiraku experiment, deciding what to try next, or when a record has no approved, falsifiable hypothesis. Stage 1 of hiraku-powers.
---
# Define the next record

Turn an idea into a child record with a falsifiable hypothesis. No source
changes, no commits. Scripts: `../hiraku-powers/PROTOCOL.md`.

1. **Ground.** `hiraku status --json`, `hiraku record tree`, then `record show`
   the active record, its root, and its siblings, and their runs. Read the code
   the idea touches. If `inprogress` lists the same idea, resume it instead. If
   there is no root yet, include one (goal, question, metrics with directions)
   in the proposal.
2. **Ask** only what grounding did not answer, one question at a time, each with
   your recommended answer ([grill-questions.md](grill-questions.md)). Stop
   when you can state the hypothesis and the pass/fail rule.
3. **Propose.** Present, keeping what the user said apart from your
   assumptions:
   - record ID (unused project-wide), parent, `related_to`;
   - `summary` (one line) and `hypothesis` (one sentence a run could
     contradict);
   - `success_criterion`: root metric, direction, and the margins that count as
     supports, refutes, or inconclusive;
   - baseline sibling, comparability constraints, out of scope.
   Every metric must be a root metric; if not, propose the root change too.
   **Cycle** (`../hiraku-powers/SKILL.md`): for the run-only and small cycles,
   draft the plan now (`hiraku-powers-plan`, steps 1-3; small cycle with
   `- Implement: inline`) and present proposal and plan together.
4. **On approval,** create the record and record the approval with the user's
   words:

```bash
hiraku record create <id> --parent <root-id> --type experiment --no-activate \
  [--related-to <active-id>] --set 'summary=...' --set 'hypothesis=...' \
  --set 'success_criterion=...' --set status=proposed
bash <scripts>/approve <id> proposal --quote "<their words>"
# run-only or small cycle: `open`, write exactly the presented plan, `save`, then
bash <scripts>/approve <id> proposal-and-plan --quote "<their words>"
```

Never pass `--commit` here and never activate the record; pass `--record <id>`
to later commands instead. If the idea splits into independent questions,
propose several records and let the user pick one.

Then continue with `hiraku-powers-plan` (full cycle) or with stage 3
(`../hiraku-powers/SKILL.md`).
