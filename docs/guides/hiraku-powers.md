# Agent record cycle (hiraku-powers)

The `hiraku-powers` skills let a coding agent (Claude Code, Codex) take one
research record from an idea to a reviewed result. You approve what will be
tested and how; the agent implements and runs it; a second, independent agent
reviews the evidence before anything is written as a result.

## Setup

`hiraku init` installs the skills. In an existing project, install or update
them with:

```bash
hiraku init --skills-only
```

This writes the core `hiraku` skill and the `hiraku-powers` skills to
`.claude/skills/` and `.agents/skills/`. The helper scripts need `bash` and
`jq`.

## Use it

Ask the agent in your own words, for example:

- "What should we try next?" or "Let's try gradient boosting as the next record."
- "Continue the hist-gb record."
- `/hiraku-powers` in Claude Code or `$hiraku-powers` in Codex.

The agent then:

1. **Defines** the record. It reads your records, runs, and code, asks only what
   it cannot find out (one question at a time, each with a suggestion), and
   proposes a child record with a **hypothesis** and a **success criterion**:
   the metric, its direction, and the margin that counts as support,
   refutation, or inconclusive. **You approve.**
2. **Plans** it: the tasks, entrypoint and arguments, branch, smoke run, full
   run, budget, and how the evidence is read. **You approve.**
3. **Implements** it: commits the change, runs a smoke run, binds the commit to
   the record, and runs the full run. It stops and asks if something would
   change the scope, the metrics, comparability, or the budget.
4. **Reviews** it with a fresh agent that has not seen the implementer's notes.
   It checks the diff, attribution, comparability with the baseline, and the
   margin against the seed standard error, and drafts a result (`supports`,
   `refutes`, or `inconclusive`) and a decision. **You approve**, and only then
   are `result` and `decision` written to the record.

An approval is your explicit yes to what was shown; the agent records it with
your words. If the plan or hypothesis changes afterwards, it has to ask again.

## Cycles

The agent picks a cycle in step 1 and tells you which one:

| Cycle | When | Approvals | Implemented by |
| --- | --- | --- | --- |
| Run-only | No code change, only arguments or config | One, for proposal and plan together | Subagent |
| Small | At most two files, no new dependency, a few runs | One, for proposal and plan together | The agent you are talking to |
| Full | Everything else | Proposal, then plan | Subagent |

Every cycle keeps the hypothesis, the success criterion, and the independent
review. For quick work without any gates, ask for it directly; the core
`hiraku` skill handles single records and runs.

## Where things are stored

- **The record** holds `hypothesis`, `success_criterion`, `status`
  (`proposed`, `planned`, `implemented`, `reviewed`), the bound `commit`, and
  finally `result` and `decision`. Its Markdown body holds the `## Plan`, with
  ticked tasks and a `### Deviations` log.
- **The workspace** `.hiraku-powers/records/<record-id>/` is git-ignored. It
  holds the ledger (approvals with your words, stage markers, run IDs) and the
  notes and drafts of the implement and review stages.

## Resuming and checking progress

The ledger is the source of truth, so a new session can pick up where the last
one stopped: ask the agent to continue, or check yourself from the project
root:

```bash
bash .claude/skills/hiraku-powers/scripts/inprogress     # unfinished records
bash .claude/skills/hiraku-powers/scripts/state <record-id>
```

`state` prints the stage (`define`, `plan`, `implement`, `review`, `present`,
`done`), whether a subagent is working, the approved mode, the branch, and the
commits.

## Good to know

- The skills never change the active record; they pass `--record` explicitly.
- A fix after a review finding becomes a new record linked with `related_to`.
  A bound commit is never replaced without your explicit request.
- Subagents cannot see your conversation. Anything that matters for the work
  must be in the record or the plan, and the agent writes your answers to its
  questions into the ledger.
- `hiraku runs --record <id>` lists every run of the tracking source; the
  skills filter by `record_id` themselves.
