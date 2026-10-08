---
name: hiraku-powers-implement
description: Use only when dispatched as the hiraku-powers implement subagent for a record whose state says implement, or as the main agent when that record's state says implement inline (small cycle). Stage 3 of hiraku-powers.
---
# Implement the record

Execute the approved `## Plan`, nothing more. As a subagent you cannot ask the
user; questions go into `implement-notes.md`. Inline (small cycle), the same
steps apply, and a STOP becomes a direct question to the user. Answers from an earlier attempt are
`user answer:` notes in `ledger.md`; follow them. Scripts and run filtering:
`../hiraku-powers/PROTOCOL.md`.

1. **Start.** `state <id>` must say `implement`; its `mode:` is what was
   approved. `open <id>` and read `body.md` and `ledger.md`; skip tasks that
   are already ticked. Check `git status --short --branch`; switch to `state`'s
   `branch:` only from a clean worktree and never discard user work. Then
   `ledger <id> --marker base`.
2. **Each task:** make the smallest change that does it, run its check, then
   tick it in `body.md` and `save <id>` (the ticks are your resume point).
   Change nothing else in the plan above `### Deviations`.
3. **Commit** once the checks pass, following the project's commit
   instructions. Every changed line must trace to a task; revert anything else.
   Skip this step when the plan changes no code.
4. **Smoke run** with `hiraku launch <entrypoint> --record <id> --commit HEAD`,
   note its run ID (`ledger <id> "smoke run <run-id>"`), confirm a tracked row
   for this record, then `bind <id>` (binds HEAD). If `bind` refuses because
   another commit is bound, stop and ask.
5. **Full run** (mode `implement-and-run` only): the plan's remaining launches
   with `--record <id>`, noting each run ID (`ledger <id> "full run <run-id>"`).
   Run long jobs in the background and watch the filtered runs; a foreground
   timeout is not a failure until the runs say so.
6. **Finish:** write `implement-notes.md` (below), then
   `ledger <id> --marker implement-done`.

**Deviations.** Fix small slips that leave scope and results unchanged (a
wrong path, a renamed function, a check command that needs a flag) and log
each under `### Deviations`. **Stop** for anything that changes scope,
hypothesis, metrics, comparability, or budget, and for failures you cannot fix
within the plan: log it, `save`, list uncommitted partial work under Side
effects (never discard it), write the notes with `Status: STOP`, and run
`ledger <id> --marker implement-stop "<reason>"`. Never write `result` or
`decision`, never rebind, never exceed the budget.

## implement-notes.md

```text
Status: DONE | STOP
Commit: <sha or none>
Runs: <ids or none>
Checks: <command -> result, per task>
Deviations: <list or none>
Side effects: <unplanned changed or untracked files, or none>
Questions for the user: <list or none>
Ideas not implemented: <list or none>
```

If the file tool refuses to write it, write it with a shell heredoc.
