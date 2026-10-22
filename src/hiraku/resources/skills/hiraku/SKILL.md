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
  goal, question, metrics, branch, shared entrypoints, and evolving findings.
  A child has one `parent_id`, may begin as an unbound proposal, may bind the
  source commit being evaluated, and may override inherited entrypoints or
  arguments. Its optional `type` describes its role, such as experiment,
  diagnostic, or ablation. `related_to` links records without inheritance.
  Fields inherit from the nearest parent definition by default; `id`,
  `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` stay local.
- **Run:** An execution trace returned by the configured tracking backend. A run
  may include record, commit, and entrypoint attribution together with
  parameters, metrics, timestamps, and an external URL.
- **Entrypoint:** A named executable definition consisting of a command argument
  list and default arguments. A child inherits parent entrypoints by name and
  can override or extend them. Select a record explicitly for run attribution.
- **Dispatch script:** A project-owned script under `hiraku/dispatch/` that
  decides where and how an entrypoint runs. The default `launch.sh` uses the
  current checkout; `worktree.sh` runs an exact commit in an isolated worktree.
  Projects may provide additional local or remote dispatchers.

The usual relationship is: a root contains child records; a child can bind
source and expose one or more effective entrypoints; a dispatch script launches
an entrypoint; instrumented code independently writes the resulting run to the tracking backend.

## Main CLI entry points

When the connected agent environment provides Hiraku MCP tools, use them for
record and context CRUD, project resources, and attributed entrypoint launches.
The MCP server uses the project directory selected by the host and applies the
same Hiraku validation and persistence rules as the Python API. Use the CLI
commands below when the relevant MCP tool is unavailable or the user asks for
CLI commands. MCP tools may be listed under names such as `record_create`,
`record_show`, or `context_list`.

Prefer `--json` for state that will be inspected or verified programmatically.

| Purpose | Main commands |
| --- | --- |
| Initialize or inspect the project | `hiraku init`, `hiraku status --json` |
| Manage records | `hiraku record ...`: `list`, `tree`, `show`, `create`, `update`, `set`, `entrypoints` |
| Launch an entrypoint | `hiraku launch ENTRYPOINT [--record ID] [--commit REF] [--dispatch NAME]` |
| Run a Snakemake workflow, observed or from a `Snakefile` | `hiraku snakemake run` / `generate` |
| Inspect tracked evidence | `hiraku runs [--record ID] [--all]` |
| Run a workflow | MCP tool named after the workflow (see `workflow_list`), then `workflow_send_event`; terminal: `hiraku workflow list`, `hiraku workflow start ID`, `hiraku workflow send-event SESSION EVENT` |
| Inspect or resume workflow sessions | MCP tools `session_list`, `session_show`; terminal: `hiraku session list`, `hiraku session show ID` |
| Manage context records | `hiraku context ...` |
| Discover exact syntax | `hiraku --help`, `hiraku COMMAND --help`, `hiraku COMMAND SUBCOMMAND --help` |

Use `record show` and `record entrypoints`
to resolve canonical records and effective executable definitions before making
changes or launching work. Use `launch --dry-run` when execution resolution
needs verification without starting the command.

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
- **Hiraku workflow:** Start or continue a workflow, such as
  `collaborative`, only when the user explicitly requests it. Discover
  workflows with the `workflow_list` MCP tool, start one with the tool named
  after it, and follow the user's constraints, permissions, budgets, and
  stopping condition in addition to the workflow's instructions.

Infer routine identifiers, summaries, hypotheses, metrics, and entrypoint
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
hiraku runs --record <record-id> --json
```

When needed, inspect the repository files required to understand available code,
entrypoints, metrics, data, and project conventions. Resume existing relevant
work instead of duplicating it. If command syntax or supported options are
uncertain, run `hiraku <command> --help`; do not guess flags.

Research records are Markdown files with YAML front matter. Preserve
existing fields and prose when updating them. Core fields are validated;
additional fields such as `hypothesis`, `result`, `conclusion`, `decision`, and
`status` are user metadata rather than an enforced state machine.
Each root and its descendants are stored at
`hiraku/records/{root_id}/{record_id}.md`. IDs are unique across the project.
Use `record show` to see effective values and their sources, or `--raw` for
the stored fields. An explicit `null` clears an inherited field; entrypoints
merge by name.

## Protect user work and use the intended branches

Before implementing or committing for a record, or switching branches, inspect
the Git state and relevant records:

```bash
git status --short --branch
hiraku record show <root-id> --json
hiraku record show <child-id> --json
```

- Put implementation commits on the record's `branch` when that
  field is set. Otherwise use the nearest inherited `branch`. Do not implement on a
  different branch merely because it is currently checked out.
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
stored in `hiraku/records/{root_id}/{root_id}.md`.

For a high-level root-creation request:

1. Check for an existing root that already represents the request.
2. Choose a concise slug when the user did not supply an ID.
3. Translate the request into a concrete goal, research question, and declared
   metrics with their optimization directions when these are known. Preserve
   the user's actual research objective; do not narrow the root to the methods
   chosen for initial examples or baselines unless the user requests that scope.
4. Create the root and verify the canonical record.

```bash
hiraku record create <root-id> --type task \
  --set 'goal=...' \
  --set 'question=...' \
  --set 'metrics=[{name: val/loss, direction: minimize}]'
hiraku record show <root-id> --json
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
`hiraku/records/{root_id}/{record_id}.md`. It may be a baseline,
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
5. Inspect the runnable code and inherited entrypoints. Declare a
   child entrypoint only when an existing one does not already describe
   the requested invocation.
6. Bind `--commit HEAD` during creation only when the record already
   represents the current source state. Leave a future idea as an unbound
   proposal and update its commit after implementation.
7. Verify the resulting record, resolved commit, and entrypoint.

```bash
hiraku record create <record-id> --parent <root-id> --type experiment \
  --set 'summary=...' \
  --set 'hypothesis=...' \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {seed: 42}}}'
hiraku record show <record-id> --json
hiraku record entrypoints <record-id> --json
```

Creating a record activates it by default. Several children may share a
commit and differ by entrypoint arguments. Follow the effective record branch
recorded for implementation; create or change that branch assignment only when
the user or the active workflow asks for it. Ordinary Git operations remain
responsible for commits and merges.

Use repeatable `--set 'key=<YAML value>'` for custom metadata and `--unset key`
to remove it. `--markdown-file path` replaces the Markdown body, so retain any
notes that are still relevant. Never rebind a completed record to make a
different source state appear to be the same attempt.

## Run records and inspect evidence

A run is an execution trace owned by CSV, MLflow, W&B, or a custom tracking
backend. Hiraku launches entrypoints and queries runs; the training or evaluation
code writes the run to its backend. `hiraku snakemake run` also writes one CSV
run per workflow execution.

For a high-level run request:

1. Resolve the exact record and entrypoint. Inspect effective entrypoints with
   `hiraku record entrypoints <record-id>`.
2. Confirm that the record is bound to the intended commit and that the
   required code, data, dependencies, and credentials are available.
3. Use `--dry-run` when it materially helps validate a nontrivial dispatch or
   argument override.
4. Launch with `--record <record-id>` so attribution is explicit.
5. Query that record's direct runs immediately afterward and report the observed
   result. A successful process without a tracked row is not a tracked run.

```bash
hiraku launch <entrypoint> --record <record-id>
hiraku runs --record <record-id> --json
```

Everything after `--` overrides entrypoint arguments:

```bash
hiraku launch train --record <record-id> -- --seed 7
```

Do not repeat an existing successful run unless the user or workflow requires a
replicate. On failure, preserve the evidence, explain whether the launch failed
or simply produced no tracked row, and stop or continue according to the user's
requested scope. Record conclusions or decisions only when requested or when
they are part of the bounded/autonomous workflow, and cite actual run IDs,
metrics, and source commits.

## Compare runs and carry out research workflows

Use root metrics and compatible runs when comparing child records. Keep data
splits, budgets, and evaluation definitions comparable, and do not infer
record attribution when it is absent.

```bash
hiraku runs --record <root-id> --all \
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

## Hiraku workflows

Workflows are project-defined procedures under `hiraku/workflows/` that guide
you step by step through the Hiraku MCP server. The user may start one with
its prompt, which shows the first instruction directly, or you start it with
the tool named after it. Start and accepted-event responses contain one instruction: its text, its
inline `data`, optional research `context`, and, unless it is final, an `events`
block that says which events it accepts:

1. Read attached research contexts and records, then carry out the instruction
   text using its `data`. MCP returns native resource links by default; fetch
   them before use to retrieve their current contents.
2. Follow any requests for user approval in the instruction text before sending
   an event. Approval is a conversational contract.
3. Send one of `events.accepted` with the `workflow_send_event` tool, never in
   the chat: its arguments as the event's schema describes, and the events
   block's `session_id` and `instruction_id` unchanged. Sending an identical
   event again is safe; a `stale` status means the instruction moved on, so
   continue from the one returned.
4. A `rejected` or `not_ready` response keeps the same instruction current; fix
   the input or check again later. An instruction without an `events` block is
   final: carry it out, then stop.

To resume, find the session with `session_list` and read its current
instruction with `session_show`; do not start a new session. Before repeating
consequential work such as creating records or launching experiments, check
whether it already exists. User instructions override workflow defaults; if
they conflict with the workflow, explain the conflict, or stop the session with
its `stop` or `finish` event when it has one. If a workflow's tool is missing, the workflow
may have failed to load or been added after the MCP server started; ask the
user to reconnect the MCP server.

## Entrypoints, dispatch, and tracking details

Entrypoints are command argument lists, not shell expressions:

```yaml
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args: {seed: 42, epochs: 40}
```

An entrypoint resolves from the selected record through its parent chain.
The nearest definition of each name wins. `--record <record-id>` attributes
the run to that record; the active record is used when no ID is supplied.

`--dispatch NAME` selects `hiraku/dispatch/NAME.sh`; the configured default is
`launch.sh`. The default dispatcher reports the current commit and clean/dirty
state. The `worktree` dispatcher runs the requested commit in a temporary
worktree. Hiraku injects `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`,
`HIRAKU_ROOT`, and `HIRAKU_ENTRYPOINT`. Dispatchers report `HIRAKU_COMMIT`
and `HIRAKU_GIT_STATE`.

The default CSV backend uses project-root `runs.csv`. MLflow and W&B require the
training code and Hiraku adapter to use the same configured backend destination.
Hiraku exposes normalized summaries; full curves and artifacts remain in the
tracking backend.

## Track Snakemake workflows

In a Snakemake workflow, records state the code and the rules connect the
steps through their files. Give each step an ordinary entrypoint on the record it belongs to, and
give the workflow record a CSV tracking backend that keeps its runs with the
records, `{type: csv, path: hiraku/runs.csv}`, so recording an execution
does not change the code files:

```yaml
entrypoints:
  train:
    command: [python, scripts/train.py]
    args: {alpha: 0.0001, epochs: 40}
```

1. Write the `Snakefile` yourself: it is one reviewable file. Use rules that
   call `hiraku launch` so the record defines the base command, and review
   extra arguments and `--config` values that override its settings.
   Start from `hiraku snakemake generate --record
   <workflow-record> --print`, which drafts one rule per entrypoint of the
   record and its children with `input:` and `output:` left to fill in, or
   from `--observed --print` when `hiraku/snakemake/observed/` holds the
   user's observed launches. Read each script's arguments to find its files,
   pass them after `--`, for example `-- --input {input[0]} --output
   {output}`, and add a `rule all`. Never put file paths only in a rule's own
   command: the record must describe the code a step runs.
2. Use observation only when the user asks for a workflow without a
   `Snakefile`, or to learn which files a step touches: `hiraku launch
   ENTRYPOINT --record RECORD --observe` (or the MCP `launch_entrypoint` tool
   with `observe: true`) records them, and `hiraku snakemake run` without a
   `Snakefile` builds the rules from them. Observation is off by default.
3. Dry-run, execute, and inspect:

```bash
hiraku snakemake run --record <workflow-record> --dry-run
hiraku snakemake run --record <workflow-record> --cores 2
hiraku runs --record <workflow-record> --json
```

Use `--command uv --command-arg run --command-arg snakemake` to run Snakemake in
the project environment, and `--config 'train=--epochs 2'` to pass extra
entrypoint arguments for a quick run.

Each execution is one CSV run of the workflow record. Its `raw.jobs` lists every
step with its rule, record, entrypoint, command, inputs, outputs, and whether it
ran; `raw.outcome` is `planned`, `succeeded`, `no-op`, `failed`, or
`interrupted`. To explain where a file came from, find the job whose `outputs`
contain it and report its record, entrypoint, and command. Steps that log their
own runs, such as MLflow training, link them through `HIRAKU_WORKFLOW_RUN_ID`.

## Register existing datasets

Register existing datasets and their artifact descriptors as ordinary records
without executing workflows, fabricating runs, or reading dataset contents.
Preview is always the default; nothing is written until `--apply`.

1. Register files under a project-relative root with `hiraku registry scan
   --root DIR` (preview) and then `--apply --reviewed-hash <content_sha256>`
   with the hash the preview printed. Revisions are `size:mtime`. Scan
   external mounts only by a `dataset_roots` name with `--external NAME`; the
   dashboard links their artifacts to workflow steps by resolved location,
   also when a `Snakefile` names them by absolute path.
2. Register descriptor-only or remote data from a manifest with
   `hiraku registry import MANIFEST` (preview) and then
   `--apply --reviewed-hash <content_sha256>`. Descriptor-only registration
   never opens artifact locations, and credentials never belong in manifests
   or records.
3. Browse registered datasets in the dashboard's Datasets view; re-importing
   identical content makes no edits, and an exhaustive rescan demotes
   disappeared artifacts to `stale` rather than deleting records.

## Finish clearly

Verify the requested end state with `show`, `list`, or `runs --json`. Report
what was created, changed, or run; include record IDs, entrypoint,
commit, run ID, and key metrics when applicable. Mention failures or missing
attribution explicitly. Stop when the user's requested scope is complete.

The Python API exposes `HirakuConfig` in `hiraku.config`; `Record`,
`Entrypoint`, `Metric`, loaders, and listing functions in `hiraku.record`;
`BaseRecord` in `hiraku.base`; `Run` in `hiraku.run`;
and `hiraku.launch.launch` for scripted use. `hiraku context` manages
supporting material. Workflows run in the current coding agent through the
Hiraku MCP server (`hiraku mcp`).
