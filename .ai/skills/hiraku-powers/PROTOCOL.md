# Hiraku powers protocol

Shared by all stages. Paths use `<skills-dir>`, the directory holding the
installed skills (`.claude/skills` or `.agents/skills`); stage skills reach this
file as `../hiraku-powers/PROTOCOL.md`.

## Workspace and scripts

Each record has a git-ignored scratch workspace at
`<project-root>/.hiraku-powers/records/<record-id>/`: `head.md` (text before the
first `##`), `summary.md`, `plan.md`, `rest.md` (every other `##` section),
`result.md`, `decision.md`, `report.md`, `findings.md`, `ledger.md`. Skills edit
these files; scripts sync them with the record through the `hiraku` CLI. Each
part holds only its own `##` section (use `###` inside `plan.md`).

Run scripts with `bash <skills-dir>/hiraku-powers/scripts/NAME`, starting every
call with `cd <project-root> &&` (a stray `cd` persists in the shell). They need
`jq` and `hiraku` (on PATH, or `uv run` in the project).

| Script | Purpose |
| --- | --- |
| `workspace ID` | Print the workspace; seed it on first use; archive it if the record was re-created |
| `pull ID [--discard \| --to DIR]` | Refresh parts from the record; refuses to overwrite unpushed edits; `--to` never touches the workspace |
| `push ID [--force] [--drop PART]` | Validate, re-split to prove the round trip, then write the body; refuses a stale body, a missing part, or silently emptying a part |
| `state ID` | Print the stage computed from the ledger and the record |
| `inprogress` | List unfinished records with their stage |
| `approve ID proposal` / `approve ID plan MODE AUTO` | Record a user approval (MODE `implement` or `implement-and-run`, AUTO `yes` or `no`); pins the plan |
| `reopen ID plan\|proposal` | Revoke an approval |
| `ledger ID "text"` / `ledger ID --marker KIND [text]` | Add a note, or a marker: `base` (once), `implement-done`, `implement-stop "reason"`, `review-drafted` |
| `bind ID [REF] [--rebind]` | Bind a verified commit (default `HEAD`); refuses to replace one |
| `clear-report ID` | Remove a stale `report.md` before dispatch |
| `finalize ID [STATUS]` | Write `result.md`, `decision.md`, and `status` (default `reviewed`) after approval |

If `push` reports a changed body: `pull ID --to <dir>`, diff against the
workspace parts, merge by hand, then `push ID --force`. Never use `--force`,
`--drop`, or `pull --discard` without looking at what is thrown away.

## State machine

The ledger is the recovery map; after context loss trust `state`, the ledger,
and `git log` over memory. Markers are written only by scripts (free text is
stored as `note:` and can never act as a marker); the latest marker wins:

`proposal approved` -> `plan approved: mode; auto-continue; plan-sha` ->
`base <sha>` -> `bound commit <sha>` -> `implement done` (or `implement stop:
reason`) -> `review drafted` -> `finalized`. `proposal reopened` and `plan
reopened` step back. The plan hash ignores ticks and `### Deviations`, so
progress is allowed but any other plan change returns the stage to `plan`.

## Subagent flow (stages 3 and 4)

1. Dispatch only if `state` says `implement`. Check that the commands the plan
   needs (tests, launches, `git commit`) are permitted for a subagent; if
   unsure, tell the user before dispatch.
2. `clear-report ID`, then dispatch the **implement subagent** with the record
   ID, project root, scripts directory, and skills directory
   (`implement/implementer-prompt.md`). Do not poll; wait for the notification.
3. Run `state`. If it still says `implement` (a `stopped:` line, or no
   `implement done`), or `report.md` is missing, `STOP`, a failed check, a failed
   run, or a run without a tracked row: relay `report.md` questions to the user,
   or `reopen plan` and go back to plan. Never continue past a STOP. If `state`
   says `review` and auto-continue is yes, go on; if no, ask.
4. Dispatch the **review subagent** (`review/reviewer-prompt.md`) only when
   `state` says `review`, never in parallel with implement. Give it the record
   ID, the `base:` and `bound:` values from `state`, and run IDs.
5. When `state` says `present`, present findings and drafts; do not re-dispatch.
   Only after approval update `summary.md`, `push`, then `finalize`.

Subagents share only the workspace files and ledger, never ask the user (they
write questions to `report.md` or `findings.md`), and never exceed the plan's
budget. Anything the user said that matters for judgment belongs in the record
or plan, because subagents cannot see the conversation.

**No subagent tool** (e.g. some Codex setups): run the stage inline as your own
brief, keeping the same files and ledger. For review, derive findings from the
diff, the runs, and the record, and do not rely on `report.md`; draft as above.

**Long runs:** a subagent's foreground command is time-limited. Start a full
run in the background and monitor it through `hiraku runs --record <id>`; never
call a foreground timeout a failure before checking `hiraku runs`.

**Independence:** the reviewer does not read `report.md`, and treats `ledger.md`
and `### Deviations` as the implementer's self-report: verify claims against
`git diff` and `hiraku runs`.
