---
name: hiraku-powers-define
description: Use when starting a new Hiraku experiment, deciding what to try next, or when a record has no approved, falsifiable hypothesis. Stage 1 of hiraku-powers.
---
# Define the next record

Turn an idea into an unbound child proposal. Create no source changes and no
commits. Questions come from [grill-questions.md](grill-questions.md). Workspace,
scripts, and the state machine: `../hiraku-powers/PROTOCOL.md`.

<HARD-GATE>
Do not create the record until the user approved the written-back understanding,
and do not start planning until they approved the proposal and you ran
`approve <id> proposal`.
</HARD-GATE>

## Process

1. **Ground.** Run `hiraku status --json`, then `record tree` and `record show`
   for the active record's root (or the root the user named). Read sibling
   results, `hiraku runs --record <id> --json`, and the code the idea touches.
   Resume existing work (`inprogress`) instead of duplicating it. If
   `record tree` is empty there is no root: create it first (goal, question,
   metrics with directions) and include it in the write-back for approval.
2. **Grill.** Ask one question at a time, each with your recommended answer.
   Skip anything the records or code already answer. Keep going until the user
   can state the hypothesis and the pass/fail criterion.
3. **Write back.** Summarize deliverable, goal, parent, hypothesis, metric and
   threshold, comparability constraints, and what is out of scope. Separate what
   the user said from your assumptions. Check that every metric in the
   hypothesis and success criterion is a root metric; if not, change the root
   metrics in the same step. Add one definition line per metric or symbol (the
   metric schema has no description field). Incorporate corrections.
4. **Propose.** After approval, check the ID is unused project-wide, then create
   the proposal. Always pass `--no-activate`: this skill never changes the
   active record, which stays the experiment the user works under.

```bash
hiraku record create <id> --parent <root-id> --type experiment --no-activate \
  --set 'summary=...' --set 'hypothesis=...'
hiraku record show <id> --json
```

5. `workspace <id>`, write `summary.md` (`- Status: proposed`, `- Hypothesis:`,
   `- Success criterion:`, parent, out of scope), and `push <id>`. Leave
   `--commit` unset. If the idea splits into independent investigations,
   propose several records and let the user choose one to continue.
6. Present the record. Only after the user approves it, run
   `approve <id> proposal` (it needs the Hypothesis and Success criterion
   lines in the pushed summary).

## Exit

Hand off to `hiraku-powers-plan`. If the user changes the proposal later,
`reopen <id> proposal`.

## Red flags

| Thought | Reality |
| --- | --- |
| "The hypothesis is implied" | Write it as a falsifiable sentence or ask. |
| "I'll ask everything up front" | One question at a time, with a recommendation. |
| "I'll check the code later" | If the code can answer it, do not ask the user. |
| "I'll activate it so later commands work" | Never. Pass `--record <id>` instead. |
