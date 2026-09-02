# Plan 02: Hiraku Command-Line Interface

## Objective

Implement the first production-quality `hiraku` command-line interface from
the fixed contracts in `README.md`, `docs/core.md`, and `docs/cli.md`, on top of
the completed core Python API from Plan 01. The milestone covers project
initialization and status, Markdown record management, active task and
experiment selection, proposals, editor integration, branch selection,
entrypoint launching, and read-only run queries.

The CLI is a deterministic adapter over the Python API. It must not introduce a
second record model, implicit behavior in the core library, or CLI-only
persistence. Active-state fallback belongs to the CLI; calls into the public
Python API must use explicit resolved objects and identifiers.

This code will receive detailed human review. It must be clean, idiomatic,
fully typed, documented, easy to test, and conservative around files, editors,
Git, subprocesses, and terminal output. Passing tests, linting, formatting, and
distribution smoke tests are baseline acceptance requirements.

Keep the README, core, installation, and CLI contracts unchanged unless
implementation reveals a direct contradiction. Resolve any newly discovered
high-level conflict with the project owner before changing documented
behavior.

## Packaging and command foundation

Add Click 8.1+ as the only new required runtime dependency and expose:

```toml
[project.scripts]
hiraku = "hiraku.cli:main"
```

Use Click for command grouping, option validation, aliases, help rendering,
exit handling, and isolated CLI tests. Do not add Rich or another presentation
framework for the PoC. Render deterministic plain-text tables internally and
use the standard library for JSON, editor argument splitting, and subprocess
coordination.

Implement the CLI primarily in one module:

```text
hiraku.cli    main(), command groups, parsing, active-state resolution,
              presentation, editor invocation, and core API orchestration
```

Do not create a `hiraku.cli` package or split entity commands across modules in
this milestone. Keep private, typed helpers in `cli.py` for repeated parsing,
mutation, rendering, and resolution mechanics. Only `hiraku.cli.main` is a
supported Python-facing CLI entrypoint; other names in the module are
implementation details and are not re-exported from `hiraku`.

The bulk of production changes in this milestone must be in `src/hiraku/cli.py`
and the corresponding CLI tests. The CLI should resolve input, apply
CLI-specific convenience, call the core, and format results. It must not
reimplement record registries, validation, serialization, active-state
invariants, Git operations, launch execution, or tracking normalization.

### Abstraction-readiness rule

Treat pressure to move substantial behavior outside `cli.py` as an abstraction
review signal. When the CLI needs a capability that is broadly meaningful to
Python callers, first determine whether the existing core already expresses it.
If it does, use it directly. If it does not, add the smallest general-purpose,
fully tested core operation and document why it belongs in the public Python
API. Do not add a CLI-only service layer to compensate for an incomplete core.

Expected core changes are small and exceptional: for example, an atomic
active-selection clear operation may be justified if record deletion otherwise
requires direct configuration-dictionary mutation. Parsing Click options,
choosing human columns, invoking `$EDITOR`, rendering tables, and translating
exceptions are CLI concerns and remain in `cli.py`.

Before implementation is accepted, review the diff by responsibility:

- most new production code is in `src/hiraku/cli.py`;
- core changes are individually justified as reusable API improvements;
- no domain behavior is duplicated between core and CLI; and
- no new CLI-specific abstraction module exists.

## Process and error boundary

The Click root command owns the process boundary:

- return status 0 for successful commands and `--help`;
- translate `HirakuError` subclasses into concise actionable messages on
  standard error with a non-zero status;
- translate Click usage errors separately so invalid options and arguments show
  the relevant command usage;
- do not expose tracebacks for expected user errors;
- preserve unexpected exceptions for development and tests rather than
  disguising programming defects;
- write human results to standard output; and
- keep JSON output free of decoration, with warnings on standard error.

Help must not discover a project, load records, invoke Git, or mutate state.
Build command registration without eager project resolution. Discover
`hiraku.yml` inside command callbacks only after parsing succeeds.

Use the help wording, usage forms, argument names, option names, aliases, and
defaults in `docs/cli.md`. Terminal wrapping and styling may follow Click, but
the documented meaning must remain exact. Set a stable `hiraku` program name in
tests so snapshots do not depend on the test runner.

## Shared CLI infrastructure

### Project context

Create a small typed command context in `cli.py` that lazily stores a discovered
`HirakuConfig`. Commands that need a project call one discovery helper.
`init` is the exception: it uses the current directory directly and must not
attempt discovery first.

Never cache records across command invocations. Resolve explicit command
arguments before active state. An unavailable documented active fallback raises
the corresponding core error; never scan other tasks to guess scope.

### JSON normalization

Implement one recursive JSON conversion path for:

- records as all structured fields plus `markdown`;
- `Run` values including complete `metrics`, `params`, and `raw` mappings;
- timezone-aware datetimes as ISO 8601 UTC strings;
- `Path` objects as strings;
- mappings, sequences, and immutable mapping proxies; and
- status and entrypoint mapping results.

Use `json.dumps()` with deterministic key behavior and no terminal markup.
Do not abbreviate IDs or remove fields in JSON mode.

### Human formatting

Implement deterministic dependency-free tables with:

- stable column order;
- a header row and consistent spacing;
- empty strings for missing optional values;
- scalar values rendered plainly and structured values rendered compactly;
- no color or terminal-width-dependent truncation in captured/non-interactive
  output; and
- ID abbreviation only in human run tables, never where it would become
  ambiguous among displayed rows.

Record `show` output is the canonical front matter followed by Markdown, using
the existing core serializer conventions. Do not create an alternative human
record syntax.

### Arbitrary field mutation

Support only explicit mutation options:

```text
--set KEY=VALUE
--unset KEY
```

Unknown CLI options are errors and are never interpreted as record keys. Do not
implement arbitrary option-name kwargs or `--delete-key`.

Parse each `--set` value with `yaml.safe_load()` so strings, numbers, booleans,
nulls, sequences, and mappings match the record's YAML data model. Require a
non-empty top-level key and the literal `KEY=VALUE` separator. Repeated
`--set`/`--unset` options preserve command-line order for deterministic
diagnostics.

Reject an invocation when:

- the same field is supplied more than once;
- a field appears through both a dedicated option and `--set`;
- a field appears through both `--set` and `--unset`;
- `id`, `task_id`, or another registry identity field would change or be
  removed; or
- the resulting record fails core validation.

Dedicated text options such as `--goal`, `--question`, `--summary`, and
`--hypothesis` always produce strings and do not undergo YAML coercion. Apply
all changes in memory, validate once, and save once so a failed update leaves
the original record unchanged.

### Markdown input

`--markdown-file PATH` reads UTF-8 text and replaces the entire Markdown body.
Report missing, unreadable, or invalidly encoded files with the path. Read and
validate all inputs before mutating or saving a record.

### Editor integration

Implement `edit` for tasks, experiments, programs, actions, and contexts:

1. Resolve the canonical record path using the same identifier and active-state
   rules as `show`.
2. Attempt to load the record. If its existing contents fail validation, emit
   a warning and continue so `edit` remains a recovery path; missing records
   and reference-resolution failures still fail normally.
3. Read `$EDITOR`; use `vim` when it is unset or blank.
4. Split the value with `shlex.split()` and reject an empty or malformed
   command.
5. Append the absolute record path and invoke an argument vector with
   `shell=False`.
6. Wait for the editor to exit.
7. Return non-zero when the editor fails.
8. On success, reload the record through its concrete type and validate its
   registry identity.

Support editor values such as `code --wait`. The editor owns its in-place file
write. If reloading or validation fails, retain the edited file for recovery,
report the validation failure, and return non-zero. Never overwrite the invalid
edit with the pre-edit contents.

## Project commands

### `hiraku init`

Call `HirakuConfig.init(Path.cwd())`. Rely on the core for atomic creation,
resources, executable dispatcher, skill installation, and duplicate-project
failure. Print a concise success message containing the absolute project marker
path. Do not create a task, experiment, Git repository, or commit.

### `hiraku status`

Discover the project and report:

- project root and `hiraku.yml` path;
- active task and experiment IDs, including explicit null/unset presentation;
- default program and tracking backend; and
- current Git branch.

Git status is informative. When the project is not inside a Git repository,
show the branch as unavailable rather than failing the otherwise valid Hiraku
status command. `--json` emits the same information as a complete object.

## Task commands

Implement the exact `create`, `list`, `show`, `update`, `edit`, `delete`, `set`,
and `entrypoints` group from `docs/cli.md`.

### Creation and branches

For `task create`:

1. Validate the ID, dedicated fields, generic fields, Markdown input, and
   branch name before persistence.
2. Select `--branch BRANCH` when provided; otherwise derive
   `hiraku/tasks/{task_id}` from configuration.
3. Set the selected branch in the task record.
4. Create and save the task with `activate=False`.
5. Reuse the selected branch when it exists; otherwise create it from current
   `HEAD` without switching.
6. Activate the task unless `--no-set` was supplied.

The `--branch` option takes a required value when present; bare `--branch` is a
usage error. `main`, another existing branch, and a new custom name are valid.
If Git validation or branch creation fails after the new record is saved,
delete only that newly created record and leave active state unchanged. Never
delete an existing or newly created Git branch as rollback. Perform validation
and conflict checks first to make partial failure exceptional.

### Listing and display

`task list` uses `list_tasks()`. Default human columns are stable and concise;
`--columns` accepts a comma-separated ordered list of core or user field names.
Missing user fields display empty. Reject an empty column name. JSON ignores
human column selection and remains complete.

`show`, `update`, `edit`, and `entrypoints` use an explicit `TASK_ID`, then the
active task. `entrypoints` lists the task's mapping without resolving or
launching commands.

### Mutation and deletion

Dedicated task field options set conventional mapping fields rather than
attributes. `delete` always requires an explicit ID, calls guarded task
deletion, and never touches backend runs or Git branches. If the deleted task
is active, clear active task and experiment state only after successful record
  deletion; add a focused core configuration operation if the core lacks a safe atomic
way to do so rather than directly rewriting configuration internals.

## Experiment and proposal commands

Register `experiment` and the exact `exp` alias against the same Click group so
behavior and options cannot drift. Implement `create`, `list`, `show`, `update`,
`edit`, `delete`, `set`, `clear`, and `entrypoints`.

### Scope resolution

Accept experiment references as either `{experiment_id}` or
`{task_id}:{experiment_id}`. Resolution order for an unqualified reference is:

1. explicit `--task`;
2. active task; then
3. error.

Reject conflicting qualified and explicit task scopes. `show`, `update`,
`edit`, and `entrypoints` may omit the experiment ID and use the active
experiment. `delete` and `set` require explicit experiment IDs as documented.
Never search across tasks.

### Creation and branches

Without `--branch`, create the experiment with no experiment-specific branch;
it remains associated with its task branch. With bare `--branch`, derive
`hiraku/experiments/{task_id}/{experiment_id}`. With
`--branch BRANCH`, select that valid custom branch.

Click must support the optional `--branch` value without consuming the required
experiment ID. Implement and test a dedicated option parser/callback if Click's
default option arity cannot represent this grammar safely. The following must
be distinct and valid:

```text
hiraku experiment create baseline
hiraku experiment create --branch baseline
hiraku experiment create --branch shared baseline
```

The first `baseline` above is the experiment ID; in the second, bare
`--branch` selects the configured default and `baseline` remains the ID; in the
third, `shared` is the branch and `baseline` is the ID. The implementation must
not guess from repository state. Normalize the argv shape deterministically
before Click binds the command arguments.

When a branch is selected:

- record its name in the experiment;
- reuse it when it already exists;
- otherwise create it from `--from REF`, or task-branch HEAD when omitted;
- reject `--from` without `--branch`;
- reject `--from` for an existing selected branch because it would have no
  effect; and
- never switch branches implicitly.

Create the experiment with `activate=False`, establish or reuse its branch, and
activate it and its task unless `--no-set` was supplied. On pre-activation
failure, remove only a record created by this invocation. Never delete a Git
branch as rollback.

Resolve `--commit REF` to a full commit through the existing experiment save
behavior. An absent commit produces a proposal.

### Mutation, activation, and proposals

`update --commit REF` stores the resolved full hash. Ordinary field mutation
must not add a special bind operation. `set` activates both the experiment and
its task; `clear` clears only the active experiment.

`proposal list` calls the normal task-scoped experiment listing and filters
records whose `commit` is absent. It returns experiment records, not a proposal
type or lifecycle status.

Experiment deletion removes only the record. If it is active, clear active
experiment state after successful deletion. Never delete its branch, commit,
or backend runs.

## Program, action, and context commands

Implement the documented `create`, `list`, `show`, `update`, `edit`, and
`delete` commands as record-only operations.

- Programs use `{program_id}` and `config.programs_dir`.
- Contexts use `{context_id}` and `config.contexts_dir`.
- Actions parse the exact `{entity}/{action_id}` form, validate both segments,
  and use `config.actions_dir`.
- `action list --entity ENTITY` restricts listing through `list_actions()`.

Use detached construction plus the canonical configured path for creation.
Reject conflicts before writing and use `Record.save()` for persistence.
Updates use the shared mutation pipeline. Deletion removes only the Markdown
record; remove newly empty record directories only when they are exactly the
canonical leaf directory and contain nothing else.

Do not execute programs, invoke actions, compose contexts, or add active
program/action/context state. `default_program` remains informational to this
CLI milestone.

## Launch command

Implement the grammar exactly:

```text
hiraku launch [HIRAKU_OPTIONS] TARGET [ENTRYPOINT_OVERRIDES...]
```

The first target token is an absolute parser boundary. Parse Hiraku options
only before it. Forward every subsequent token to the entrypoint override
parser, including tokens named `--task`, `--commit`, `--dispatch`, or `--help`.
Do not enable Click's normal interspersed-option behavior for this command.

### Target resolution

Support:

```text
{entrypoint}
{experiment_id}:{entrypoint}
```

Resolve task scope from `--task`, then active task. A plain target loads the
task and resolves only its entrypoint mapping. An experiment target resolves
the experiment within the selected task and resolves only its mapping. Do not
merge namespaces or fall back between them.

Pass the resolved `Entrypoint`, target name, argument overrides, and explicit
attribution into `hiraku.launch.launch()`. Never add active-state lookup to the
core launch function.

### Attribution

- A plain task target receives its task ID.
- It also receives the compatible active experiment by default.
- An experiment target receives both its task and experiment IDs.
- `--experiment ID` attributes a task entrypoint to that explicit experiment
  without changing the declaration source.
- `--no-experiment` suppresses experiment attribution but retains task
  attribution.
- `--unassigned` suppresses both IDs after records have been used to resolve
  the declaration.
- `--unassigned` conflicts with `--experiment` and `--no-experiment`.
- Explicit launch options never change active configuration.

A bound attributed experiment supplies its stored commit when `--commit` is
absent. An explicit commit for a bound experiment must resolve to the same hash
or fail before dispatch. A proposal may launch from the working tree or an
explicit commit, but launching never writes its commit.

### Entrypoint overrides

Parse the post-target token sequence into a mapping:

- `--key value` creates a scalar string override;
- repeated keys create an ordered sequence;
- a flag with no following value creates `True`;
- `--key=value` is accepted as the equivalent scalar form;
- positional post-target values and a value preceding any flag are errors; and
- `--` may be used as a literal boundary only if the entrypoint override
  contract is extended explicitly; do not invent pass-through behavior now.

Normalize hyphens in CLI keys to underscores before passing overrides to the
core renderer. Preserve occurrence order. Do not YAML-coerce entrypoint
override values; command arguments remain strings unless the stored defaults
already provide typed values.

Return the underlying dispatch process status. Stream or relay captured stdout
and stderr consistently, without wrapping a scheduler result or persisting a
launch record.

## Run commands

`run list` resolves `--task`, then active task. `--experiment` is an explicit
filter within that task; the active experiment is never an implicit run filter.
Call `Task.runs()` with explicit query options and let backend selection honor
task-over-project precedence.

Parse comma-separated `--metrics` and `--params` as ordered unique names and
reject empty names. These options affect human columns only; JSON always emits
complete normalized `Run` objects.

Sorting behavior:

- `--ascending` and `--descending` are mutually exclusive;
- either direction requires `--sort`;
- an explicit direction is passed to the backend query;
- without an explicit sort, preserve core timestamp-then-ID order;
- missing and non-numeric sort values remain after ranked values; and
- `--limit` must be a non-negative integer.

When no explicit metric columns are supplied, use the selected experiment's
metric declarations if an experiment filter is present; otherwise use the task
declarations. Experiment declarations replace task declarations, while absent
experiment declarations inherit them. Use metric direction as the default sort
direction only when a sort metric is selected without an explicit direction.

Human tables include run ID, optional experiment, timestamp, commit, selected
parameters, and selected metrics. CSV values remain strings except normalized
timestamps; do not reinterpret them. The CLI remains query-only and exposes no
logging, mutation, deletion, or cross-task listing command.

## Safety and transactional behavior

- Validate all arguments and load all referenced records before mutations.
- Use core atomic writes for configuration and records.
- Never invoke a shell for Git, editor, launch, or file operations.
- Never delete branches, commits, runs, or unrelated directories.
- Never overwrite a record through creation.
- On a multi-step create failure, roll back only a record created by that exact
  invocation when safe; report any retained Git branch explicitly.
- Do not mutate active configuration until the record and required Git
  operation succeed.
- Keep `--help`, validation failures, and JSON queries side-effect free.

## Test plan

### Parser and help

- Root command, every group, every subcommand, and `exp` alias snapshots
- Help without a project and without filesystem or Git side effects
- Required arguments, defaults, repeatable options, and mutual exclusions
- Unknown options rejected rather than becoming record fields
- Every command example from `docs/cli.md`
- Stable program name and presentation-normalized snapshot fixtures

### Project and output

- Initialization success, duplicate failure, and installed resources
- Status from nested directories, unset active state, non-Git projects, and Git
  projects
- Human output on stdout, errors on stderr, and exit statuses
- Complete valid JSON for records, status, entrypoints, datetime-bearing runs,
  paths, and immutable mappings
- No decoration, truncation, or abbreviation in JSON

### Shared record behavior

- `--set` YAML strings, numbers, booleans, null, sequences, and mappings
- `--unset`, duplicate keys, dedicated-option conflicts, and immutable fields
- Unknown options, malformed `KEY=VALUE`, and validation rollback
- UTF-8 Markdown replacement and read errors
- Editor selection, `vim` fallback, multi-token `$EDITOR`, exit failure,
  pre-existing invalid-record recovery, successful validation, and retained
  invalid edits
- Create conflicts, atomic updates, deterministic lists, and deletion scope

### Tasks

- Default and custom task branches
- New and existing branch reuse, invalid names, and non-Git failure
- Activation defaults, `--no-set`, and incompatible experiment clearing
- Explicit versus active show/update/edit/entrypoints resolution
- User-field columns, complete JSON, guarded deletion, and active deletion

### Experiments and proposals

- Qualified, explicit-task, and active-task scope resolution
- Conflicting scopes and absence of cross-task guessing
- No branch, bare `--branch`, custom branch, new branch, and existing reuse
- Optional branch-value parsing beside the required experiment ID
- `--from` validation and task-branch default source
- Commit resolution, proposals, activation, clearing, and deletion
- Exact `experiment`/`exp` behavioral parity

### Agent records

- Program and context CRUD/edit at canonical paths
- Action reference parsing, entity filtering, and canonical identity validation
- No active-state or execution side effects

### Launching

- Target as the Hiraku parsing boundary
- Post-target Hiraku-named options forwarded as entrypoint overrides
- Scalar, repeated, boolean, and equals-form override parsing
- Task versus experiment namespace separation
- Active, explicit, task-only, experiment, and unassigned attribution
- Option conflicts and no active-state mutation
- Bound commit inheritance and explicit mismatch rejection
- Working-tree and isolated-commit dispatch
- Dispatcher selection, output relay, exit-code propagation, and failures
- No persisted launch or scheduler-specific result

### Runs

- Task fallback and explicit experiment filtering without active filtering
- CSV and real local-SQLite MLflow queries through the installed adapters
- Metric/parameter column selection and complete JSON
- Metric inheritance and experiment precedence
- Ascending/descending validation, declared directions, missing values, and
  limits
- Deterministic human tables and collision-safe ID abbreviation
- Backend failures translated at the CLI boundary

### Distribution and quality

- `hiraku` console script present in wheel and source installations
- Clean-wheel CLI initialization, record CRUD, help, and launch smoke tests
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest`
- `uv build`, followed by installation and CLI execution in a clean temporary
  environment

## Delivery sequence

1. Add Click, the console script, `src/hiraku/cli.py`, its root error boundary,
   and the isolated CLI test harness.
2. Implement private typed helpers in `cli.py` for discovery, JSON, human
   formatting, field mutation, Markdown input, and editor invocation.
3. Implement `init` and `status` with snapshots and JSON tests.
4. Implement task CRUD, active selection, entrypoints, and branch behavior.
5. Implement experiment CRUD, aliases, proposal filtering, optional branch
   parsing, commit handling, and active selection.
6. Implement program, action, and context record commands.
7. Implement launch boundary parsing, target resolution, attribution, argument
   overrides, and dispatch result handling.
8. Implement run querying, metric-aware columns and sorting, tables, and JSON.
9. Run all documented examples as parser/integration tests and complete help
   snapshots.
10. Build wheel and source distributions, install into a clean environment, and
    execute the end-to-end CLI smoke suite.

## Acceptance criteria

- All commands and subcommands documented in `docs/cli.md` are implemented.
- The bulk of production code added by this milestone is contained in
  `src/hiraku/cli.py`; any core changes are small, reusable, and explicitly
  justified by an abstraction gap.
- Generated help matches the documented command semantics and is snapshot
  tested.
- Active-state resolution occurs only in the CLI; core launch remains explicit.
- Arbitrary record fields require `--set`; unknown kwargs and `--delete-key` do
  not exist.
- Editor commands follow `$EDITOR`, fall back to `vim`, wait, validate, and
  preserve invalid edits.
- Task and experiment branch behavior matches the finalized asymmetric grammar
  and safely reuses selected existing branches.
- Launch parsing preserves the target boundary and returns the dispatcher
  process result.
- Run commands query CSV and MLflow without adding mutation APIs.
- Human and JSON output are deterministic and tested.
- The implementation adds no deferred agent execution, lifecycle, dashboard,
  scheduler, backend mutation, or cross-task query behavior.
- Ruff, pytest, build, clean-install, and console-script smoke checks pass.

## Explicitly deferred

- Agent, program, and action execution
- Context composition and active context sets
- Autonomous loops and lifecycle enforcement
- Dashboards and long-running interfaces
- Cross-task run queries
- Backend logging, mutation, and deletion
- Scheduler-specific submission objects
- Shell completion generation and plugin command discovery
- YAML-only records, alternate record suffixes, and record relocation
