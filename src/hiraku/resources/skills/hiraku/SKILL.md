---
name: hiraku
description: Use Hiraku to structure and carry out data-science and machine-learning research from focused record work through bounded investigations and autonomous research workflows.
---
# Hiraku

Hiraku provides a toolkit for agentic data science.

Hiraku connects research goals, records, source code, execution, and findings.
Treat Hiraku as a research workspace rather than a closed command menu. Translate
the user's research intent into the appropriate combination of records, source
work, launches, evidence, and findings, including requests that enter midway
through an existing record lifecycle.

## Core concepts

- **Record:** A research scope or focused investigation. A root may record the
  goal, question, branch, and evolving findings.
  A child has one `parent_id`, may begin as an unbound proposal, and may bind
  the source commit being evaluated. Its
  `type` describes its role, such as experiment, diagnostic, or
  ablation. `parent_id` and `related_to` are persisted relationships, independent
  of filesystem placement.
  The active record is stored in `hiraku.yml`. Named leaderboards live in
  `hiraku/leaderboards/<name>.yml` and contain source and metric configuration.
- **Run:** An execution trace returned by the configured tracking backend. A run
  may include record, commit, and invocation attribution together with
  parameters, metrics, timestamps, and an external URL.
- **Command:** Explicit argv passed to each `hiraku job run` invocation. Commands
  are not stored in records.
- **Job:** A local PSI/J execution attempt in the current project working tree,
  with a saved JSON snapshot and output. Foreground waits; background returns
  after submission. Scripts log `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID` on runs.
  Hiraku saves output, returns the command's exit code, and supplies tracking
  attribution for that invocation.
- **Dispatch script:** An optional project script under `hiraku/dispatch/`
  that prepares the runtime before running argv. Commands run directly by
  default; Hiraku does not install a generic dispatcher. Tracking backends own
  experimental measurements.

## Main CLI entry points

When the connected agent environment provides Hiraku MCP tools, use them for
record CRUD, project resources, workflows, and foreground command execution.
The MCP server uses the project directory selected by the host and applies the
same Hiraku validation and persistence rules as the Python API. Use the CLI
commands below when the relevant MCP tool is unavailable or the user asks for
CLI commands. MCP tools may be listed under names such as `record_create`,
`record_show`, or `record_list`.

Prefer `--json` for state that will be inspected or verified programmatically.

| Purpose | Main commands |
| --- | --- |
| Initialize or inspect the project | `hiraku init`, `hiraku status --json` |
| Manage records | `hiraku record ...`: `list`, `tree`, `show`, `create`, `update`, `set` |
| Run a command | `hiraku job run [--record ID] [-- COMMAND ...]` |
| Submit a job | `hiraku job submit [--executor NAME] [--record ID] -- COMMAND ...` |
| Manage leaderboards | `hiraku leaderboard list`, `show NAME`, `create NAME --source TYPE`, `edit NAME`, `delete NAME` |
| Inspect tracked evidence | `hiraku leaderboard runs [--leaderboard NAME] [--record ID]` |
| Run a workflow | MCP tools `workflow_list`, `workflow_start`, `workflow_status`, and `workflow_resume`; collaborative approvals use `workflow_approval`; terminal: `hiraku workflow list`, `hiraku workflow start ID`, `hiraku workflow status SESSION_ID`, `hiraku workflow resume SESSION_ID` |
| Manage records, including supporting material | `hiraku record ...` |
| Discover exact syntax | `hiraku --help`, `hiraku COMMAND --help`, `hiraku COMMAND SUBCOMMAND --help` |

Use `record show` to inspect a record's fields and Markdown.
Use MCP `leaderboard_list` or `hiraku leaderboard list --json` to discover
leaderboards. Select one with
`--leaderboard NAME` when several exist. Leaderboards only query results; configure logging
destinations in training code or its environment.

## Operating scope

Use Hiraku at the scope the user requested: a single record operation, a bounded
sequence of research steps, or an explicitly requested autonomous workflow.

## Development version and feedback

Hiraku is under active development. Be transparent when its behavior,
instructions, or records do not support the requested workflow cleanly:

- Flag apparent bugs, inconsistencies, missing capabilities, and unclear or
  incomplete documentation to the user. Describe the expected behavior, the
  observed behavior, and the evidence that distinguishes a Hiraku problem from
  an experiment or repository failure.
- If continuing requires a workaround that affects scope, provenance, records,
  reproducibility, or results, explain its tradeoffs and agree on it with the
  user before applying it. Do not silently edit records or bypass attribution to
  conceal a limitation.
- Encourage the user to report reproducible problems at the
  [Hiraku issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues),
  and offer to prepare a concise reproduction and expected behavior. Open or
  update an issue only with the user's authorization.
- When a missing Hiraku feature would significantly simplify or strengthen the
  requested work, suggest the capability and discuss it with the user. With
  their authorization, create a feature request that explains the research use
  case and desired outcome rather than prescribing an unnecessarily narrow
  implementation.
- Treat missing, misleading, or opaque Hiraku instructions and documentation,
  including this skill, as feedback worth surfacing. Suggest a concrete
  documentation or skill improvement when it would prevent the same problem.

## Follow the user's scope

Classify the request before acting:

- **Brainstorming:** Support the user with grounded suggestions, but do not create
  or modify any records, source code, or runs until the user explicitly requests
  it. Ask questions if the user intent is unclear.
- **One action:** Do only the requested operation, verify it, report the result,
  and stop. For example, "create a record" does not also implement or run
  it, and "run this record" does not create another record or rewrite
  its record.
- **Bounded workflow:** Complete every requested stage in order, such as creating
  a research root, defining child records, running them, and comparing results.
  Do not add stages the user did not request.
- **LangGraph workflow:** Start or continue a research loop only when the user
  explicitly requests it. Read the workflow definition and follow its guidance
  and the user's constraints, permissions, budgets, checkpoints, and stopping
  condition. For collaborative workflow approval gates, call
  `workflow_approval` and wait for its structured MCP elicitation result.

Infer routine identifiers, summaries, hypotheses, metrics, and command
arguments from the user's request and the repository. Ask a question only when
the missing choice would materially change the research outcome or authorized
scope. If a question can be answered by exploring the codebase or surrounding
project context, explore the codebase instead. Never treat a broad research
goal as permission for unrelated external actions, destructive Git operations,
or unlimited resource use.

## Ground every action

Start by discovering the project and current selections:

```bash
hiraku status --json
```

Then inspect only the state relevant to the request:

```bash
hiraku record list --json
hiraku record tree
hiraku record show <record-id> --json
hiraku leaderboard runs --record <record-id> --json
```

When needed, inspect the repository files required to understand available code,
commands, metrics, data, and project conventions. Resume existing relevant
work instead of duplicating it. If command syntax or supported options are
uncertain, run `hiraku <command> --help`; do not guess flags.

Research records are Markdown files with YAML front matter. Preserve
existing fields and prose when updating them. Core fields are validated;
additional fields such as `hypothesis`, `result`, `conclusion`, `decision`, and
`state` are user metadata rather than an enforced state machine.
`status` is reserved for the OKF lifecycle: `draft`, `stable`, or `deprecated`.
Use `state` for workflow-defined research progress such as `drafted` or `analyzed`.
Choose an OKF `id`: a bundle-relative path under `hiraku/records/`, without `.md`.
Supply the optional display label with `--title`. Use `id`
without `.md` for lookups, parent references, run attribution, and later commands.
IDs are derived from file placement and are not persisted in front matter.
Inspect `record list --json` for concept IDs.
Use `record show` to inspect local fields and Markdown. Run queries use named leaderboards in `hiraku/leaderboards/<name>.yml`, containing a
`source` mapping and optional ordered `metrics` with optimization directions.
Parameters are discovered automatically. Queries list runs with a record ID,
even if its file is missing. Use `--include-unattributed` (MCP:
`include_unattributed: true`) to include all source runs. `--record` remains an
exact attribution filter. Cycles and unfinished links do not prevent normal record I/O.

## Protect user work and use the intended branches

Before implementing or committing for a record, or switching branches, inspect
the Git state and relevant records:

```bash
git status --short --branch
hiraku record show <root-id> --json
hiraku record show <child-id> --json
```

- Put implementation commits on the record's `branch` when that field is set.
  Do not implement on a different branch merely because it is currently checked out.
- Switch branches only from a clean Git worktree. Never discard, reset, stash,
  commit, or modify user work to make a switch possible. If reaching the
  intended branch would affect existing work, stop and ask the user how to
  proceed.
- Hiraku records may be committed or ignored; this is a project and user choice.
  Inspect tracked files, ignore rules, and nearby history, then follow the
  existing convention. Do not begin tracking ignored records or ignore tracked
  records unless the user requests that change.
- When committed Hiraku records are combined across branches, ensure every
  child record is available on the intended integration branch, even when its
  implementation commit lives on another branch. Do not leave the only
  committed copy of a child record on its implementation branch.
- Hiraku reads only records visible in the current worktree. Do not infer that a
  record is absent merely because the current branch does not expose it. Before
  listing or comparing committed records across a root, switch safely to the
  intended branch and verify that its child records are present. If a clean
  switch is not possible, stop and ask the user how to proceed.

## Create and maintain research roots

A root record is a research scope, goal, and evolving account of findings,
stored at the chosen relative path under `hiraku/records/`.

For a high-level root-creation request:

1. Check for an existing root that already represents the request.
2. Choose a concise human-readable title when the user did not supply one.
3. Translate the request into a concrete goal and research question. Configure comparison metrics and their
   optimization directions in a leaderboard when known. Preserve
   the user's actual research objective; do not narrow the root to the methods
   chosen for initial examples or baselines unless the user requests that scope.
4. Create the root and verify the canonical record.

```bash
hiraku record create tasks/question --title "Research question" --type task \
  --set 'goal=...' \
  --set 'question=...'
hiraku record show tasks/question --json
```

Creating a record activates it by default. Use `--no-activate` only when it
should not become active. Hiraku does not switch Git branches automatically.
Store `branch` with `--set` only when the user or project workflow specifies one.

Use `hiraku record update` for requested changes or evidence-backed summaries.
Do not summarize experiments that have not been run or conclusions that the
available runs do not support. For substantial changes to a root record, inspect
and preserve the existing Markdown, prepare the revised body, and apply it with
`--markdown-file` rather than overwriting useful context.

## Create and maintain child records

A child is a focused investigation within a root, stored in
the chosen relative path under `hiraku/records/`. It may be a baseline,
hypothesis, diagnostic, ablation, or other investigation. A child without
a source commit is a proposal; binding a commit links it to the code being
evaluated.

For high-level child-record work:

1. Resolve the intended parent from the request or active record and inspect it.
2. Check that the record ID is not already in use anywhere in the project.
3. Capture a short summary and a falsifiable hypothesis when the request
   provides enough information. Brainstorming may produce one or more unbound
   proposals without changing source code.
4. When asked to implement an existing proposal, inspect its hypothesis and the
   relevant code, make and verify the focused change, commit it if authorized,
   then bind that commit with `hiraku record update <record-id> --commit
   HEAD`.
5. Inspect the runnable code and decide which argv to submit to `hiraku job run`.
6. Bind `--commit HEAD` during creation only when the record already
   represents the current source state. Leave a future idea as an unbound
   proposal and update its commit after implementation.
7. Verify the resulting record and resolved commit.

```bash
hiraku record create experiments/baseline --parent tasks/question --type experiment \
  --set 'summary=...' \
  --set 'hypothesis=...' \
  --commit HEAD
hiraku record show experiments/baseline --json
hiraku job run --record experiments/baseline -- uv run --locked python train.py --seed 42
```

Creating a record activates it by default. Several children may share a
commit and use different explicit command arguments. Follow the effective record branch
recorded for implementation; create or change that branch assignment only when
the user or selected workflow asks for it. Ordinary Git operations remain
responsible for commits and merges.

Use repeatable `--set 'key=<YAML value>'` for custom metadata and `--unset key`
to remove it. `--markdown-file path` replaces the Markdown body, so retain any
notes that are still relevant. Never rebind a completed record to make a
different source state appear to be the same attempt.

## Run records and inspect evidence

A run is an execution trace owned by CSV, MLflow, or W&B. Hiraku launches
commands and queries runs; the training or evaluation code writes the run to
its backend.

For a high-level run request:

1. Inspect the record, revision, runnable code, dependencies, and tracking destination.
2. Inspect source changes and confirm data and environments are available at
   their configured paths.
3. Run with explicit record attribution and inspect output and the exit code.
   Then query tracked runs; a successful command without measurements is not
   experimental evidence.

```bash
hiraku job run --record <record-id> -- uv run --locked python train.py --seed 42
hiraku leaderboard runs --record <record-id> --json
```

Tokens after `--` replace the complete invocation:

```bash
hiraku job run --record <record-id> -- uv run --locked python train.py --seed 7
```

Do not repeat an existing successful run unless the user or workflow requires a
replicate. On failure, preserve the evidence, explain whether the launch failed
or simply produced no tracked row, and stop or continue according to the user's
requested scope. Record conclusions or decisions only when requested or when
they are part of the bounded/autonomous workflow, and cite actual run IDs,
metrics, and source commits.

## Compare runs and carry out research workflows

Use leaderboard metric declarations and compatible runs when comparing child records. Keep data
splits, budgets, and evaluation definitions comparable, and do not infer
record attribution when it is absent.

```bash
hiraku leaderboard runs \
  --params seed,epochs \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss --ascending --json
```

For a bounded research workflow, repeat the relevant cycle until the user's
requested outcome is reached: inspect prior evidence, formulate one focused
experiment, make only the necessary code change, bind the resulting source
commit when appropriate, launch it, inspect its attributed run, and record an
evidence-backed result. Respect any requested maximum experiments, runs, time,
cost, or hardware budget.

For a named workflow, inspect the root, workflow definition, child history,
runs, Git state, and required files before starting it. Resume incomplete work
with the same workflow session and follow its checkpoints and stopping rules.
User instructions override workflow defaults.

Workflows are project-defined LangGraph procedures under `hiraku/workflows/`.
Start one with `workflow_start(workflow_id, arguments)`. Follow the current
interrupt payload and its response schema. Resume the same run with
`workflow_resume(workflow_session_id, response, checkpoint_id)` using the checkpoint ID
returned by start or status; inspect it without advancing with
`workflow_status(workflow_session_id)`. The workflow session ID is the LangGraph thread ID, so do not
start a new run to continue an interrupted workflow. Before repeating
consequential work such as creating records or launching experiments, check
whether it already exists. User instructions override workflow defaults; if
they conflict with a workflow, explain the conflict and do not bypass its
validation. If a workflow is missing, check `workflow_list` or reconnect MCP
after adding it to the project.

For an interrupt requesting approval, call `workflow_approval` with the workflow session ID,
interrupt ID, proposal, and proposed response. It uses MCP elicitation to wait
for approve, revise, or stop. Pass the returned result to `workflow_resume`.
If elicitation is cancelled or declined, tell the user and leave the workflow
paused; do not resume it or launch the experiment. Do not replace elicitation
with an ordinary assistant question.

Commands are not record metadata. Pass the complete invocation directly after
`--`, for example `hiraku job run --record <record-id> -- uv run --locked python
train.py --seed 42`. A dispatcher can prepare repeatable project-specific
runtime behavior around that invocation.

Commands execute in the current project working tree, including uncommitted
changes. `hiraku job run` waits until the command exits; `hiraku job submit` returns
after submission. Commands have closed stdin and do not support interactive input.

Hiraku injects `HIRAKU_RECORD_ID` (empty for unattributed jobs) and `HIRAKU_JOB_ID`.
Log them as CSV
`record_id` / `hiraku_job_id`, or MLflow tags and W&B config values
`hiraku.record_id` / `hiraku.job_id`. Runs carry their own attribution and can be
queried without local job history. Records and tracking runs hold user-managed
code provenance; Jobs do not collect it.
Tracking destinations remain
user-managed. `hiraku job submit` returns after
submission; `hiraku job list/show` reads saved jobs under `.hiraku/jobs/`.
An omitted `directory` leaves the execution directory to the executor; local jobs
inherit the submitting process's cwd. Explicit paths refer to the execution side.
Explicit `env` values are stored in the job spec and saved metadata.
`run` uses native PSI/J `LocalJobExecutor`, waits, and saves its final status in
`job.json`. The CLI and MCP default submission executor, `local-detached`, subclasses
`LocalJobExecutor` and starts a detached shell launcher that records `result.json`.
Show/list combine saved metadata with the launcher's result. Read `stdout.log`
and `stderr.log` directly; foreground `job run` also streams output. There is no
reattachment or cancellation from another process.
Remote submissions retain their native ID without polling for completion.
Renku status, output, and cancellation use native `rnk` commands.
Use a project dispatcher to prepare the runtime when needed.

CSV sources require a `path`, relative to the project root or absolute;
`${NAME}` references expand from the environment when querying runs.
Tracking backends generate
and own run IDs. Tracking adapters read record and commit attribution from run
metadata. Tracking code and adapters must use the same destination. Hiraku does
not create tracking runs automatically; curves and artifacts remain with the
tracker.

## Finish clearly

Verify the requested end state with `show`, `list`, or `leaderboard runs --json`. Report
what was created, changed, or run; include record IDs, command,
commit, run ID, and key metrics when applicable. Mention failures or missing
attribution explicitly. Stop when the user's requested scope is complete.

The Python API exposes `Project` in `hiraku.project`; `Record`,
record loaders and listing functions in `hiraku.record`;
`Leaderboard` and `Metric` in `hiraku.tracking`; use `Leaderboard.create()`,
`load()`, `list()`, and `delete()` for definitions;
`Run` in `hiraku.tracking`; and `Job.create(command, record_id=..., project=...)`
in `hiraku.jobs`, followed by `job.submit(executor="local-detached")` or blocking
`job.run()`. Pass another executor name or PSI/J instance to select it.
Each job permits one execution. Use `Job.load()` and `Job.list()` to inspect
saved execution results, and ordinary file tools for saved output.
Records with `type: context` store supporting material. Workflows run in the
current coding agent through the Hiraku MCP server (`hiraku mcp serve`). V1 does not
provide a `hiraku-agent` runner; the current coding agent executes the skill.
