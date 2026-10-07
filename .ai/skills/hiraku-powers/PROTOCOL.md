# Hiraku powers protocol

`<skills-dir>` is the directory holding the installed skills (`.claude/skills`
or `.agents/skills`); `<scripts>` is `<skills-dir>/hiraku-powers/scripts`. Run
every script as `cd <project-root> && bash <scripts>/NAME ...` (shell state
does not persist between calls). They need `bash`, `jq`, and `hiraku` (on
PATH, or through `uv run` in the project).

## Where things live

- **Record fields:** `summary`, `hypothesis`, `success_criterion`, `status`,
  `commit`, `result`, `decision`. Set them with `hiraku record update --set`;
  the scripts set `status` (`planned`, `implemented`, `reviewed`) and `commit`.
- **Record body:** `## Plan` plus any free notes. Edit it as `body.md` in the
  workspace: `open <id>`, edit, `save <id>`. Never pass `--markdown-file`
  yourself.
- **Workspace** `<project-root>/.hiraku-powers/records/<id>/` (git-ignored):
  `body.md`, `ledger.md`, `implement-notes.md`, `review-notes.md`, `result.md`,
  `decision.md`.
- **Runs:** `hiraku runs --record <id> --json` returns every run of the tracking
  source, so filter: `... | jq '[.[] | select(.record_id == "<id>")]'`.

## Scripts

| Script | Purpose |
| --- | --- |
| `state ID` | Stage, `dispatched`, `mode`, `auto-continue`, `implement`, `branch`, `base`, `bound`, approval quotes. Read-only |
| `inprogress` | Unfinished records and their stages |
| `open ID [--discard]` | Print the workspace and refresh `body.md` from the record |
| `save ID [--force]` | Write `body.md` to the record; refuses if the record changed since `open` |
| `approve ID proposal\|plan\|proposal-and-plan --quote T` | Record the user's approval and pin what it covers |
| `reopen ID plan\|proposal` | Revoke an approval before changing what it covers |
| `ledger ID "text"` | Add a note (`user answer: ...`, `smoke run <run-id>`, ...) |
| `ledger ID --marker M [reason]` | Stage markers; `ledger` without arguments lists them |
| `bind ID [REF]` | Bind a verified commit (default `HEAD`); never replaces one |
| `finalize ID [STATUS]` | Store `result.md` and `decision.md` in the record |

A script that refuses an out-of-order step exits 7 and names the stage it
needs and why. Do what the message says; do not work around it.

## Stages

`define` -> `plan` (proposal approved) -> `implement` (plan approved) ->
`review` (`implement-done`) -> `present` (`review-drafted`) -> `done`
(`finalize`). The proposal approval pins `hypothesis` and `success_criterion`;
the plan approval pins the plan above `### Deviations`, ignoring checkbox
ticks. Any other change to them sends the stage back to `define` or `plan`.
After a lost context, trust `state`, `ledger.md`, and `git log` over memory.
