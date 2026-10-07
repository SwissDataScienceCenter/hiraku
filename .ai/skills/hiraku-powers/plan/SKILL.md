---
name: hiraku-powers-plan
description: Use after a Hiraku proposal is approved and before any code changes, or when a record has a hypothesis but no approved plan. Stage 2 of hiraku-powers.
---
# Plan the record

Write a plan that a subagent can execute without asking anything. No source
changes. Scripts: `../hiraku-powers/PROTOCOL.md`.

1. **Ground.** `state <id>` (stage `plan`), `hiraku record show <id> --json`,
   `hiraku record entrypoints <id> --json`, the code to change, and
   `git status --short --branch`.
   - **Branch:** use `state`'s `branch:`. If reaching it would need a dirty
     worktree switched or would hide untracked records, ask the user now.
   - **Tracking:** confirm the entrypoint writes a run row with the root metrics
     and that the parameter under test shows up in the run's params; if not,
     plan that as a task.
2. **Choose the leanest design:** an argument over a code change, a code change
   over a new module. Include only what the hypothesis needs.
3. **Write it.** `open <id>`, put a `## Plan` from
   [plan-template.md](plan-template.md) into `body.md` (keep other sections),
   and `save <id>`. Every task names its files and a check; the entrypoint, args,
   branch, commit message, smoke and full run, budget, and evidence rule are
   fixed. Leave `### Deviations` last and empty.
4. **Present** the plan. Mode defaults to `implement-and-run`, auto-continue to
   `yes`, and implement to `subagent`; ask about them only if the full run is
   expensive or the user wants to look before review. Edit the plan's lines to
   match, save, then, after the user's yes:

```bash
bash <scripts>/approve <id> plan --quote "<their words>"
```

`approve` prints the mode and auto-continue it pinned. Then dispatch the
implement subagent (`../hiraku-powers/SKILL.md`). If planning shows the
hypothesis is wrong, `reopen <id> proposal` and go back to define.

| Thought | Reality |
| --- | --- |
| "I'll decide that while coding" | The subagent cannot ask. Decide it now. |
| "I'll add a flag for flexibility" | Not needed by the hypothesis, not in the plan. |
| "A small edit after approval is fine" | `reopen`, edit, present, approve again. |
