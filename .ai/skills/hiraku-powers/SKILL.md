---
name: hiraku-powers
description: Use when the user wants to propose, design, build, or evaluate the next Hiraku record or experiment, says "next record" or "what should we try next", or resumes a record midway.
---
# Hiraku powers

One record, four stages. The user approves stages 1 and 2 one by one; stages 3
and 4 run as subagents and the user approves once, at the end of stage 4. Load
the core `hiraku` skill first; it owns CLI syntax, branch safety, attribution,
and scope rules. This skill defines the sequence. Workspace, scripts, ledger,
are in [PROTOCOL.md](PROTOCOL.md); how to dispatch stages 3 and 4 is in
[DISPATCH.md](DISPATCH.md) (main agent only). Read PROTOCOL.md now.

| Stage | Skill | Output | User approves |
| --- | --- | --- | --- |
| 1. Define | `hiraku-powers-define` | Unbound child proposal with a falsifiable hypothesis | Understanding and proposal |
| 2. Plan | `hiraku-powers-plan` | `## Plan` in the record body | Plan, mode, auto-continue |
| 3. Implement | `hiraku-powers-implement` (subagent) | `report.md`, verified commit bound to the record, optional run | Nothing mid-stage; stops on deviation |
| 4. Review | `hiraku-powers-review` (subagent) | Findings, draft `result.md` and `decision.md` | Writing result and decision |

<HARD-GATE>
A reply approves only the stage actually presented. Do not start stage N+1, or
write its artifacts, before the user approved stage N. Approval exists only as
a ledger marker that `approve` writes with the user's own words (`--quote`).
`approve` records an approval the user gave; running it without their explicit
yes in this conversation fabricates one, and the quote is shown to them. Earlier
approvals, "go ahead with everything", and a pushed plan do not count.
To change an approved plan or proposal, `reopen` it before editing and ask
again; if `state` already says `plan` or `define` after an edit, just present it
again and re-approve. Read-only
exploration is always allowed. Never skip a stage because the work looks small.
</HARD-GATE>

## Find the entry stage

This skill family never changes the active record (`define` creates with
`--no-activate`), so the active record stays the experiment you work under.
`hiraku status --json` shows it; if it is a child experiment, `record show` it
too, along with its root, siblings, and runs. If no record is active: with one
root use it, with several ask the user (recommend the most recently worked one).
To resume,
list unfinished records and ask the script, which reads the ledger and the
record's stored fields (not inherited values):

```bash
cd <project-root> && bash <skills-dir>/hiraku-powers/scripts/inprogress
cd <project-root> && bash <skills-dir>/hiraku-powers/scripts/state <record-id>
```

`state` prints `stage:` as one of:

- `define`: no approved proposal. Go to define (`summary-has-hypothesis-and-criterion`
  says whether the summary is ready for approval).
- `plan`: proposal approved, no valid plan approval, or the plan changed after
  approval (the `note:` says so).
- `implement`: dispatch the implement subagent (never a second one while
  `dispatched: yes`; wait or ask the user); relay `stopped:` first if present.
- `review`: dispatch the review subagent.
- `present`: drafts exist; present them, do not re-dispatch.
- `done`: finished; a new idea starts a new record in define.

Say the detected stage aloud so the user can override it. The parent of a new
record is the active experiment's root, linked to the active experiment with
`related_to` when that is a child; the user may name another parent or choose the
active experiment itself in the grill.

## Rules across stages

- Stay at the requested scope; the core skill's scope rules apply.
- Hidden complexity moves you back a stage, never forward: `reopen`, say so, stop.
- Never rebind a bound record's commit. A fix after a review blocker is a new
  related record (`related_to`) through `define`; `bind --rebind` only with the
  user's explicit approval.
- Project instructions (`CLAUDE.md`, `AGENTS.md`) win over default attribution
  or commit-message conventions, e.g. a forbidden `Co-Authored-By` line.
- Never hand-edit the record body or pass `--markdown-file` yourself. Write the
  workspace files and use the scripts.
- The record body starts with `## Summary` (format in
  `../hiraku-powers-plan/plan-template.md`); each stage updates it before
  handing off. Keep it first and under ten lines.
- Files under `hiraku/records/` and `.hiraku-powers/` belong to the scripts.
  Never revert or `git checkout` them, and commit them only if the project
  already tracks records.
- Surface Hiraku bugs and gaps as the core skill describes.

## Red flags

| Thought | Reality |
| --- | --- |
| "It is a small tweak, skip the proposal" | A record without a hypothesis cannot be reviewed. Define it. |
| "The plan is obvious, start coding" | Approval of the proposal is not approval of the plan. |
| "The plan is pushed, so it is approved" | Only `state` saying `implement` counts. |
| "They said go ahead earlier" | Approval covers the stage presented then. Ask again after a change. |
| "I only tweaked the plan a little" | Any change fails the pinned hash. Present it and re-approve. |
| "The run looks good, write the conclusion" | Conclusions are drafted in review and written on approval. |
| "I'll bind the commit that matches" | Never rebind to make attempts look identical. |
| "No subagent tool, so skip the stage" | Run it inline per DISPATCH.md; never skip it. |
