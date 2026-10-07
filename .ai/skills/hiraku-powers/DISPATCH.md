# Dispatching the subagent stages

For the main agent only; the subagents read `PROTOCOL.md` and their own skill.
Scripts, ledger markers, and the state machine are in [PROTOCOL.md](PROTOCOL.md).

## Flow (stages 3 and 4)

1. Dispatch only if `state` says `implement` and `dispatched: no`. If it says
   `dispatched: yes` and the subagent is no longer running (it crashed or timed
   out without a marker), write `ledger ID --marker implement-stop "<reason>"`
   yourself, then relay and re-dispatch as in step 3. Never `reopen` while
   `dispatched: yes` (the script refuses). Check that the commands
   the plan needs (tests, launches, `git commit`) are permitted for a subagent;
   if unsure, tell the user before dispatch.
2. `clear-report ID`, then `ledger ID --marker implement-started`, then dispatch
   the **implement subagent** with the record ID, project root, scripts
   directory, and skills directory
   (`<skills-dir>/hiraku-powers-implement/implementer-prompt.md`). Do not poll;
   wait for the notification.
3. Run `state` and read `report.md`, then:
   - missing report, `STOP`, a failed check or run, or a run without a tracked
     row: relay the report's questions to the user. Write each answer with
     `ledger ID "user answer: ..."` before re-dispatching, because the
     implementer reads the ledger and nothing else. If the plan must change,
     `reopen ID plan` and go back to plan. Never continue past a STOP.
   - `state` says `review` and auto-continue is yes: go on.
   - `state` says `review` and auto-continue is no: ask the user first.
4. Dispatch the **review subagent**
   (`<skills-dir>/hiraku-powers-review/reviewer-prompt.md`) only when `state`
   says `review` and `dispatched: no`, never in parallel with implement. Run
   `ledger ID --marker review-started` first, and give the subagent the record ID
   and the `base:` and `bound:` values from `state`. It derives run IDs itself
   from `hiraku runs --record <id>`. Do not mention `report.md`. If it ends
   without drafts and `state` still says `dispatched: yes`, close it with
   `ledger ID --marker review-stop "<reason>"` and re-dispatch.
5. When `state` says `present`, present the findings and drafts, plus suggested
   follow-up proposals; do not re-dispatch. Only after the user approves,
   update `summary.md` (`Result`, `Evidence`, `Status: reviewed`, `Next`), `push`,
   and `finalize`. `push` and `finalize` are run by the main agent only. A fix
   after a blocker is a new related record through define; never rebind.

Subagents share only the workspace files and ledger, never ask the user (they
write questions to `report.md` or `findings.md`), and never exceed the plan's
budget. Anything the user said that matters for judgment belongs in the record,
the plan, or a `user answer:` ledger note, because subagents cannot see the
conversation.

**No subagent tool** (e.g. some Codex setups): run the stage inline as your own
brief, keeping the same files and ledger. For review, derive findings from the
diff, the runs, and the record, and do not rely on `report.md`. Tell the user the
review was not independent, since you had already read the report.

**Long runs:** a subagent's foreground command is time-limited. Start a full
run in the background and monitor it through `hiraku runs --record <id>`; never
call a foreground timeout a failure before checking `hiraku runs`. Note every
smoke and full run ID with `ledger <id> "smoke run <run-id>"`, since smoke runs
carry no tag in `hiraku runs`.

**Independence is honor-based:** `report.md` sits in the same directory as the
reviewer's files, so the rule that the reviewer never reads it is an instruction,
not a barrier. The reviewer treats `ledger.md` and `### Deviations` as the
implementer's self-report and verifies claims against `git diff` and
`hiraku runs`.
