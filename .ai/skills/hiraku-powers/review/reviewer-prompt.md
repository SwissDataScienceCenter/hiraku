# Reviewer prompt

Dispatch one fresh subagent only when `state <id>` says `review`. Do not pass
or mention the implement report.

```text
Review a completed Hiraku record. Read-only except for the workspace drafts
named below and the ledger; never run push, finalize, bind, or launch.

Record: <record-id>
Project root: <absolute path>
Skills dir: <absolute path to .claude/skills or .agents/skills>
Scripts: <skills dir>/hiraku-powers/scripts
Base sha (state `base:`): <sha>   Bound commit (state `bound:`): <sha>
Derive run IDs yourself: `hiraku runs --record <record-id> --json`.
Read first: <skills dir>/hiraku-powers-review/SKILL.md, then
<skills dir>/hiraku-powers/PROTOCOL.md

Start every script call with `cd <project root> &&` and run it with bash.
Get plan.md and ledger.md from `bash <scripts>/workspace <record-id>`.

Do NOT read report.md. Treat ledger.md and ### Deviations as the
implementer's self-report and verify them against `git diff` and
`hiraku runs`.

Check plan fidelity, attribution, comparability with the baseline, margin
against the standard error, independence of evidence, support for each claim,
waste, and record hygiene. Report findings with severity (blocker, major,
minor) and evidence.

Write in the workspace: findings.md, result.md (supports | refutes |
inconclusive plus the one-sentence reason with run IDs and metrics), and
decision.md, then `ledger <record-id> --marker review-drafted`. Do not
finalize. You cannot ask the user: put questions in findings.md.
```
