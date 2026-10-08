---
name: hiraku-powers
description: Use when the user wants to propose, design, build, or evaluate the next Hiraku record or experiment, says "next record" or "what should we try next", or resumes a record midway.
---
# Hiraku powers

One record, four stages. Load the core `hiraku` skill first; it owns CLI syntax,
branch safety, attribution, and scope. Scripts and the workspace are in
[PROTOCOL.md](PROTOCOL.md).

| Stage | Skill | Who | Output | User approves |
| --- | --- | --- | --- | --- |
| 1. Define | `hiraku-powers-define` | you | Child record with `hypothesis` and `success_criterion` | The proposal |
| 2. Plan | `hiraku-powers-plan` | you | `## Plan` in the record body | The plan |
| 3. Implement | `hiraku-powers-implement` | subagent | Bound commit, runs, `implement-notes.md` | Nothing; it stops on a real deviation |
| 4. Review | `hiraku-powers-review` | subagent | `review-notes.md`, draft `result.md` and `decision.md` | Writing result and decision |

## Cycles

Pick the cycle in define and say which one you picked:

- **Run-only:** no code change (an argument or config change on an existing
  entrypoint). Define and plan are approved together; a subagent runs it.
- **Small:** at most two files, no new dependency, a handful of runs. Define and
  plan are approved together, and you implement inline (`- Implement: inline`).
- **Full:** everything else. Separate approvals; a subagent implements.

Every cycle keeps the hypothesis, the success criterion, and the independent
review subagent. For no gates at all, use the core `hiraku` skill instead.

<HARD-GATE>
Do not start a stage before the previous one is approved. An approval is the
user's explicit yes to what you presented, recorded with
`approve ... --quote "<their words>"`; never run `approve` without it. To change
an approved proposal or plan, `reopen` it, edit, present it, and ask again.
</HARD-GATE>

## Find the stage

```bash
bash <scripts>/inprogress        # unfinished records
bash <scripts>/state <record-id>
```

Say the stage aloud so the user can correct it, then continue there:
`define` or `plan` -> that skill; `implement` or `review` -> dispatch below;
`present` -> present the drafts; `done` -> a new idea starts a new record.

The active record (`hiraku status --json`) is the experiment the user works
under; this skill never changes it. A new record's parent is the active
record's root, with `--related-to <active-id>` when the active record is a child,
unless the user picks another parent. With no active record and several roots,
ask.

## Dispatch stages 3 and 4

Subagents see only the record, the workspace, and the ledger, never this
conversation. Anything the user said that matters belongs in the record, the
plan, or a `ledger <id> "user answer: ..."` note before dispatch.

1. **Implement.** When `state` says `implement` and `dispatched: no`, run
   `ledger <id> --marker implement-started`. With `implement: inline`, follow
   `hiraku-powers-implement` yourself; on a STOP, ask the user directly. Otherwise
   check that the plan's commands (tests, launches, `git commit`) are permitted
   for a subagent and dispatch with
   [implement/implementer-prompt.md](../hiraku-powers-implement/implementer-prompt.md);
   wait for the notification.
2. **After implement,** run `state` and read `implement-notes.md`:
   - `Status: STOP` or no notes: relay the questions, write each answer as a
     `user answer:` note, and re-dispatch. If the plan must change, `reopen
     <id> plan`. If the subagent ended without a marker (`dispatched: yes`),
     close it first with `ledger <id> --marker implement-stop "<reason>"`.
   - `state` says `review`: continue if `auto-continue: yes`, else ask first.
3. **Review** (always a fresh subagent, in every cycle). Run
   `ledger <id> --marker review-started` and dispatch with
   [review/reviewer-prompt.md](../hiraku-powers-review/reviewer-prompt.md). Do
   not pass or mention `implement-notes.md`. A reviewer that left no drafts is
   closed with `ledger <id> --marker review-stop "<reason>"`.
4. **Present** (`state` says `present`): show `review-notes.md`, `result.md`,
   `decision.md`, and follow-up ideas. Only after the user approves, run
   `finalize <id>`.

**No subagent tool:** run the stage yourself from its skill, with the same files
and markers, and tell the user the review was not independent.

## Rules

- A fix after a review finding is a new related record through define; never
  rebind a bound commit unless the user explicitly asks.
- Hidden complexity moves you back a stage (`reopen`), never forward.
- Project instructions (`CLAUDE.md`, `AGENTS.md`) win over default commit
  conventions.
- `hiraku/records/` and `.hiraku-powers/` are written by Hiraku and the scripts;
  never revert them, and commit records only if the project already tracks them.
- Report Hiraku bugs as the core skill describes.
