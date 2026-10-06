---
name: hiraku-powers-plan
description: Use after a Hiraku proposal is approved and before any code changes, or when a record has a hypothesis but no approved plan. Stage 2 of hiraku-powers.
---
# Plan the record

Specify exactly how the record will be executed, so implementation needs no
decisions. Write the plan into the record body using
[plan-template.md](plan-template.md). Make no source changes. Workspace,
scripts, and the state machine: `../hiraku-powers/PROTOCOL.md`.

<HARD-GATE>
Do not start implementation until the user approved the written plan, the
execution mode, and auto-continue, and you ran `approve <id> plan ...`. The
approval pins the plan: any later change needs `reopen` and a new approval.
</HARD-GATE>

## Process

1. **Ground.** `state <id>` (should say `plan`), then `hiraku record show <id>
   --json` and `hiraku record entrypoints <id> --json`. Read the code the change
   touches, the inherited entrypoints, and `git status --short --branch`.
   Resolve the effective `branch`.
   **Branch check:** if the worktree is dirty or the records are untracked,
   switching or creating a branch can hide them from Hiraku. List the options
   and ask now; do not defer to implement.
   **Tracking check:** confirm the code writes a tracked run. Compare the
   `runs.csv` schema the installed CLI expects with what the project
   environment's `hiraku` writes (e.g. `record_id` vs `task_id`); report a
   mismatch as a Hiraku issue.
2. **Prefer the leanest design.** Argument change over code change, code change
   over new module. List only what the hypothesis needs.
3. **Fill the template.** Tasks are small, each with its files and a concrete
   check. Name the smoke run and the full run, the dispatcher, the commit
   strategy, the budget, and the evidence that decides pass, fail, or
   inconclusive.
4. **Write it.** Run `workspace <id>`; write `plan.md` (starting `## Plan`) and
   refresh `summary.md` (status `planned`). Other sections stay untouched in
   `rest.md`. Then `push <id>` and verify with `hiraku record show <id> --json`.
5. **No decisions left.** Before presenting, check: every task names files and a
   check; entrypoint, args, dispatcher, branch, commit message, smoke and full
   run, and budget are fixed; instrumentation is planned; side effects are
   listed. A subagent cannot ask the user, so an open decision costs a round trip.
6. **Present** the plan and ask for approval, the execution mode, and
   **auto-continue to review: yes or no**. Put both in `### Execution`, `push`,
   and only after the user says yes run
   `approve <id> plan <implement|implement-and-run> <yes|no>`. A pushed plan
   alone is a draft; `state` shows `implement` only after `approve`.

## Exit

When `state` says `implement`, dispatch the implement subagent per the flow in
PROTOCOL.md. If the user declines, stay in plan. If planning shows the
hypothesis is wrong or unclear, `reopen <id> proposal` and go back to
`hiraku-powers-define`.

## Red flags

| Thought | Reality |
| --- | --- |
| "I'll decide that while coding" | Decide it now or it is a deviation later. |
| "I'll add a flag for flexibility" | Not in the hypothesis means not in the plan. |
| "The smoke run is cheap, skip it" | The plan must name it or state in writing why it is omitted. |
| "A one-word plan edit after approval is fine" | The pinned hash will fail it. `reopen`, present, re-approve. |
