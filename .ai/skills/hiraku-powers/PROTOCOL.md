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
| `workspace ID` | Print the workspace; seed it on first use; archive it (to `.hiraku-powers/archive/`) if the record was re-created |
| `pull ID [--discard \| --to DIR]` | Refresh parts from the record; refuses to overwrite unpushed edits; `--to` never touches the workspace |
| `push ID [--force] [--drop PART]` | Validate, re-split to prove the round trip, then write the body; refuses a stale body, a missing part, or silently emptying a part |
| `state ID` | Print the stage computed from the ledger and the record, plus `dispatched:`, `mode:`, `base:`, `bound:`, approval quotes |
| `inprogress` | List unfinished records with their stage |
| `approve ID proposal --quote T` / `approve ID plan MODE AUTO --quote T` | Record an approval with the user's words (MODE `implement` or `implement-and-run`, equal to the plan's `- Mode:` line; AUTO `yes` or `no`, equal to its `- Auto-continue to review:` line); pins the proposal or plan |
| `reopen ID plan\|proposal` | Revoke an approval (not allowed once finalized) |
| `ledger ID "text"` / `ledger ID --marker KIND [text]` | Add a one-line note, or a marker: `base` (once), `implement-started`, `implement-done`, `implement-stop "reason"`, `review-started`, `review-stop "reason"`, `review-drafted` |
| `bind ID [REF] [--rebind]` | Bind a verified commit (default `HEAD`; same commit is a no-op); refuses to replace one; stage `implement` only |
| `clear-report ID` | Remove a stale `report.md` before dispatch |
| `finalize ID [STATUS]` | Write `result.md`, `decision.md`, and `status` (default `reviewed`); stage `present` only |

Scripts refuse out-of-order steps (exit 7 names the stage that is needed and
why), so a shortcut through the scripts needs the whole ordered sequence, each
step visible in the ledger. `reopen plan` is only allowed from `implement`,
`review`, or `present`; a subagent that stopped or returned without a marker is
closed by the main agent with `ledger ID --marker implement-stop "reason"`.

If `push` reports a changed body: `pull ID --to <dir>`, diff against the
workspace parts, merge by hand, then `push ID --force`. Never use `--force`,
`--drop`, or `pull --discard` without looking at what is thrown away.

## State machine

The ledger is the recovery map; after context loss trust `state`, the ledger,
and `git log` over memory. Scripts write markers, one timestamped line each. Free
text is stored as `note:` with newlines flattened, lines without a timestamp are
ignored, and each marker is parsed with one anchored pattern, so words inside a
note or an approval quote cannot act as a marker or override its fields. This
protects against mistakes made through the scripts; it does not stop anyone who
edits `ledger.md` directly. The latest marker wins:

`proposal approved` -> `plan approved: mode; auto-continue; plan-sha` ->
`implement started` (main agent, at dispatch) -> `base <sha>` ->
`bound commit <sha>` -> `implement done` (or `implement stop: reason`) ->
`review started` -> `review drafted` -> `finalized`. `proposal reopened` returns to `define`; `plan
reopened` lowers the stage to `plan` only from `implement` or later.

Both approvals pin a hash. The plan hash covers everything above the first
`### Deviations` heading, ignoring ticks, so a plan edit above it returns the
stage to `plan`. `approve plan` therefore requires exactly one `### Deviations`
heading, as the last `###` heading and (at the first approval) empty, so no plan
content sits outside the hash at that point. The log itself is never pinned (a
re-approval may keep earlier progress in it, and text appended there later is
not detected), so the reviewer checks it against the diff. A plan also needs
exactly one `- Mode:` and one `- Auto-continue to review:` line. Changing the proposal's `- Hypothesis:` or `- Success criterion:` line
returns the stage to `define`. Those two lines must stay byte-identical when
`summary.md` is rewritten for status, evidence, or results; change them only
through `reopen proposal`.
