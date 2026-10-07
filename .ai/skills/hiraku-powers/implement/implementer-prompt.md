# Implementer prompt

Fill the placeholders and dispatch one fresh subagent per record. Run
`clear-report <id>` and `ledger <id> --marker implement-started` first, and only
if `state <id>` says `implement` and `dispatched: no`. Give the
subagent nothing else; it reads the rest from the workspace.

```text
Implement one approved Hiraku record plan, nothing more.

Record: <record-id>
Project root: <absolute path>
Skills dir: <absolute path to .claude/skills or .agents/skills>
Scripts: <skills dir>/hiraku-powers/scripts
Read first: <skills dir>/hiraku-powers-implement/SKILL.md, then
<skills dir>/hiraku-powers/PROTOCOL.md

Start every script call with `cd <project root> &&` and run it with bash.
Get plan.md and ledger.md from `bash <scripts>/workspace <record-id>`.

Rules:
- Smallest change that completes each plan task. No refactors, cleanups, flags,
  or abstractions the plan does not list.
- Verify each task with its check before the next; tick it in plan.md and add a
  `ledger <record-id> "note"` line. Change nothing else in plan.md except
  ticks and ### Deviations.
- Follow project instructions (CLAUDE.md, AGENTS.md) for commits.
- Smoke run with `--commit HEAD` before `bind`; never rebind a commit.
- Obey `state`'s `mode:`: `implement` allows the plan's smoke run, no full run;
  `implement-and-run` allows the full run too. Never exceed the budget.
- Answers the user gave after an earlier STOP are `user answer:` notes in
  ledger.md; follow them. Note each run ID with `ledger <record-id> "smoke run <run-id>"` (or "full run").
- Markers only through scripts: `ledger <record-id> --marker base` at start,
  `--marker implement-done` or `--marker implement-stop "<reason>"` at the end.
- You cannot ask the user. On a deviation, failed check, failed run, or run
  without a tracked row: record it under ### Deviations, push, write
  report.md with Status: STOP, add the stop marker, and stop.
- hiraku/records/ and .hiraku-powers/ belong to the scripts; never revert them.
- Never write result or decision.

Finish by writing report.md in the workspace in this exact format:
Status: DONE | STOP / Commit / Runs / Checks / Deviations / Side effects /
Questions for the user / Ideas not implemented.
```
