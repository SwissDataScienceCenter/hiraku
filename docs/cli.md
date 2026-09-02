# Hiraku CLI

The `hiraku` command-line interface is a deterministic client of the public
Python API. It manages project configuration and Markdown records, resolves
active-state conveniences, queries tracking backends, and delegates execution
to `hiraku.launch`. It does not define a second data model.

The usage forms, arguments, options, command summaries, defaults, and behavior
below are public CLI specification. Terminal width, indentation, option
ordering, and styling are implementation details.

## Common conventions

Every command and subcommand accepts `--help`. Help exits successfully without
project discovery or mutation. Successful commands exit with status 0;
validation, discovery, filesystem, Git, launch, and backend failures exit
non-zero. Human output goes to standard output and actionable errors to
standard error.

Tasks, experiments, programs, actions, and contexts are Markdown records with
YAML front matter. Mutations validate records and write them atomically. Direct
editing remains supported. Record commands use these options where applicable:

```text
  --markdown-file PATH  Read the Markdown body from PATH.
  --set KEY=VALUE       Set a field; repeatable. VALUE is parsed as YAML.
  --unset KEY           Remove a field; repeatable and update-only.
```

Entity-specific options set conventional user fields. `show` writes front
matter followed by Markdown. `--json` emits complete structured fields plus
`markdown`; list commands emit a list of complete objects. Human-only column
selection and ID abbreviation never remove JSON fields. Warnings accompanying
JSON go to standard error.

Unknown options are errors; they are never converted into record keys. A field
may not be supplied through both a dedicated option and `--set`, or through
both `--set` and `--unset`, in one invocation. `id` and task-scoping identity
fields cannot be changed or removed.

Explicit identifiers and options take precedence over active configuration.
When a documented active-state fallback is unavailable, the command fails
rather than searching across tasks.

Every record group provides `edit`. It resolves the canonical record path using
the same identifier and active-state rules as `show`, runs `$EDITOR` with that
path, and waits for it to exit. Pre-existing validation errors produce a
warning but do not prevent the editor from opening, making `edit` the recovery
path for malformed records. An unset or blank `$EDITOR` falls back to `vim`.
The editor value is split into an argument vector without shell evaluation, so
values such as `code --wait` are supported. After a successful editor exit,
Hiraku reloads and validates the record. Invalid edits remain on disk for
recovery, but the command reports the validation error and exits non-zero.

## Top level

```text
Usage: hiraku COMMAND [ARGS...]

Manage Hiraku projects, records, launches, and tracked runs.

Commands:
  init        Initialize a Hiraku project.
  status      Show project and active-state information.
  task        Manage tasks and the active task.
  experiment  Manage experiments and the active experiment.
  exp         Alias for experiment.
  proposal    List proposed experiments.
  program     Manage program records.
  action      Manage action records.
  context     Manage context records.
  launch      Resolve and launch an entrypoint.
  run         Query tracked runs.

Options:
  --help  Show this message and exit.
```

## Project initialization

```text
Usage: hiraku init

Initialize a Hiraku project in the current directory.

Create hiraku.yml, the default metadata directories, editable bundled programs
and actions, the default dispatcher, and the repository-local /hiraku skill.
Fail if hiraku.yml already exists. Do not create or activate a task or
experiment.
```

### Notes

`hiraku.yml` is the fixed project marker. With the default configuration,
metadata is stored under `hiraku/`. Initialization leaves active task and
experiment unset.

### Examples

```bash
hiraku init
```

## Project status

```text
Usage: hiraku status [OPTIONS]

Show the discovered Hiraku project and its active state.

Options:
  --json  Emit complete status information as JSON.
  --help  Show this message and exit.
```

### Notes

Discovery searches upward from the current directory for `hiraku.yml`. Status
shows the project root, active task and experiment, default program, tracking
backend, and current Git branch. Unset optional selections are not errors.

### Examples

```bash
hiraku status
hiraku status --json
```

## Tasks

```text
Usage: hiraku task COMMAND [ARGS...]

Manage task records and the active task.

Commands:
  create       Create and validate a task.
  list         List tasks.
  show         Show a task.
  update       Update and validate a task.
  edit         Edit a task in the default editor.
  delete       Delete a task that has no experiments.
  set          Set the active task.
  entrypoints  List a task's resolved entrypoint commands.
```

```text
Usage: hiraku task create [OPTIONS] TASK_ID

Create and validate a task, then activate it by default.

Arguments:
  TASK_ID  New task identifier.

Options:
  --goal TEXT            Set the conventional goal user field.
  --question TEXT        Set the conventional question user field.
  --summary TEXT         Set the conventional summary user field.
  --markdown-file PATH   Read the Markdown body from PATH.
  --set KEY=VALUE        Set an additional field; repeatable.
  --branch BRANCH        Use BRANCH; default to hiraku/tasks/{task_id}.
  --no-set               Do not activate the created task.
  --help                 Show this message and exit.
```

```text
Usage: hiraku task list [OPTIONS]

List tasks.

Options:
  --columns NAME[,NAME...]  Select columns in human output.
  --json                    Emit complete task records as JSON.
  --help                    Show this message and exit.
```

```text
Usage: hiraku task show [OPTIONS] [TASK_ID]

Show a task; default to the active task.

Options:
  --json  Emit the complete task record as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku task update [OPTIONS] [TASK_ID]

Update and validate a task; default to the active task.

Options:
  --goal TEXT            Replace the conventional goal user field.
  --question TEXT        Replace the conventional question user field.
  --summary TEXT         Replace the conventional summary user field.
  --markdown-file PATH   Replace the Markdown body from PATH.
  --set KEY=VALUE        Set a field; repeatable.
  --unset KEY            Remove a field; repeatable.
  --help                 Show this message and exit.
```

```text
Usage: hiraku task edit [TASK_ID]

Edit a task record; default to the active task.
```

```text
Usage: hiraku task delete TASK_ID

Delete a task record that has no experiments.

Arguments:
  TASK_ID  Task to delete; required even when a task is active.
```

```text
Usage: hiraku task set TASK_ID

Set the active task and clear an incompatible active experiment.
```

```text
Usage: hiraku task entrypoints [OPTIONS] [TASK_ID]

List a task's entrypoints; default to the active task. Human output renders each
stored command with its default arguments. `--json` emits the complete original
declaration mapping.

Options:
  --json  Emit the entrypoint mapping as JSON.
  --help  Show this message and exit.
```

### Notes

With default configuration, creation writes
`hiraku/tasks/{task_id}/task.md`. `goal`, `question`, and `summary` are user
fields, not fixed Python attributes. Missing fields render as empty table
cells. Task deletion fails while experiments exist and never deletes runs or
Git branches. A task always records a branch. Omitting `--branch` uses
`hiraku/tasks/{task_id}`; when the option is present, its value is required.
The selected branch may already exist.

### Examples

```bash
hiraku task create heart-disease --goal "Develop a prediction model"
hiraku task update heart-disease --set owner=research --unset obsolete_field
hiraku task show
```

## Experiments

```text
Usage: hiraku experiment COMMAND [ARGS...]

Manage task-scoped experiment records and the active experiment.

Commands:
  create       Create and validate an experiment.
  list         List experiments for a task.
  show         Show an experiment.
  update       Update and validate an experiment.
  edit         Edit an experiment in the default editor.
  delete       Delete an experiment record.
  set          Activate an experiment and its task.
  clear        Clear the active experiment.
  entrypoints  List an experiment's resolved entrypoint commands.
```

`hiraku exp` is an exact alias. Its usage may show the invoked alias.

```text
Usage: hiraku experiment create [OPTIONS] EXPERIMENT_ID

Create and validate an experiment, then activate it and its task by default.

Arguments:
  EXPERIMENT_ID  New experiment ID, optionally qualified as
                 {task_id}:{experiment_id}.

Options:
  --task TASK_ID         Select the task for an unqualified ID; default to the
                         active task.
  --summary TEXT         Set the conventional summary user field.
  --hypothesis TEXT      Set the conventional hypothesis user field.
  --commit REF           Resolve and store REF as the experiment commit.
  --branch [BRANCH]      Use an experiment branch; without BRANCH, use
                         hiraku/experiments/{task_id}/{experiment_id}.
  --from REF             Start a new branch from REF; default to task-branch
                         HEAD. Valid only with --branch.
  --markdown-file PATH   Read the Markdown body from PATH.
  --set KEY=VALUE        Set an additional field; repeatable.
  --no-set               Do not activate the created experiment or task.
  --help                 Show this message and exit.
```

```text
Usage: hiraku experiment list [OPTIONS]

List experiments for a task.

Options:
  --task TASK_ID            Select the task; default to the active task.
  --columns NAME[,NAME...]  Select columns in human output.
  --json                    Emit complete experiment records as JSON.
  --help                    Show this message and exit.
```

```text
Usage: hiraku experiment show [OPTIONS] [EXPERIMENT_ID]

Show an experiment; default to the active experiment.

Arguments:
  EXPERIMENT_ID  ID optionally qualified as {task_id}:{experiment_id}.

Options:
  --task TASK_ID  Select the task for an unqualified ID.
  --json          Emit the complete experiment record as JSON.
  --help          Show this message and exit.
```

```text
Usage: hiraku experiment update [OPTIONS] [EXPERIMENT_ID]

Update and validate an experiment; default to the active experiment.

Options:
  --task TASK_ID         Select the task for an unqualified ID.
  --summary TEXT         Replace the conventional summary user field.
  --hypothesis TEXT      Replace the conventional hypothesis user field.
  --commit REF           Resolve and store REF as the experiment commit.
  --markdown-file PATH   Replace the Markdown body from PATH.
  --set KEY=VALUE        Set a field; repeatable.
  --unset KEY            Remove a field; repeatable.
  --help                 Show this message and exit.
```

```text
Usage: hiraku experiment edit [OPTIONS] [EXPERIMENT_ID]

Edit an experiment record; default to the active experiment.

Options:
  --task TASK_ID  Select the task for an unqualified ID.
  --help          Show this message and exit.
```

```text
Usage: hiraku experiment delete [OPTIONS] EXPERIMENT_ID

Delete an experiment record.

Options:
  --task TASK_ID  Select the task for an unqualified ID.
  --help          Show this message and exit.
```

```text
Usage: hiraku experiment set [OPTIONS] EXPERIMENT_ID

Activate an experiment and its task.

Options:
  --task TASK_ID  Select the task for an unqualified ID.
  --help          Show this message and exit.
```

```text
Usage: hiraku experiment clear

Clear the active experiment without changing the active task.
```

```text
Usage: hiraku experiment entrypoints [OPTIONS] [EXPERIMENT_ID]

List experiment entrypoints; default to the active experiment. Human output
renders each stored command with its default arguments. `--json` emits the
complete original declaration mapping.

Options:
  --task TASK_ID  Select the task for an unqualified ID.
  --json          Emit the entrypoint mapping as JSON.
  --help          Show this message and exit.
```

### Notes

Experiment IDs are task-scoped. A qualified ID supplies its task. Otherwise,
scope resolves from `--task`, then active task; no cross-task search occurs.
Default record location is
`hiraku/tasks/{task_id}/experiments/{experiment_id}.md`.

An experiment without a commit is a proposal. The CLI resolves `--commit REF`
against the Git repository containing the project root and passes the resulting
full lowercase hash to the Git-independent experiment record API. Setting
`commit` through `--set` is rejected; use `--commit REF`. `--unset commit` remains
available to turn a bound experiment back into a proposal. Bare `--branch`
selects `hiraku/experiments/{task_id}/{experiment_id}`; `--branch BRANCH`
selects any valid branch. Hiraku creates a missing selected branch and reuses
an existing one. `--from` applies only when creating it. Hiraku does not
implicitly switch, merge, delete, or resolve conflicts.

### Examples

```bash
hiraku experiment create logistic-baseline --hypothesis "A linear baseline is competitive"
hiraku experiment create heart-disease:random-forest --branch --from HEAD
hiraku experiment update logistic-baseline --commit HEAD
```

## Proposals

```text
Usage: hiraku proposal COMMAND [ARGS...]

Inspect proposed experiments.

Commands:
  list  List experiments without a commit.
```

```text
Usage: hiraku proposal list [OPTIONS]

List experiments without a commit for a task.

Options:
  --task TASK_ID  Select the task; default to the active task.
  --json          Emit complete experiment records as JSON.
  --help          Show this message and exit.
```

### Notes

A proposal is only an experiment whose `commit` is absent, not a separate
record type or lifecycle state.

### Examples

```bash
hiraku proposal list
hiraku proposal list --task heart-disease --json
```

## Programs

```text
Usage: hiraku program COMMAND [ARGS...]

Manage program records.

Commands:
  create  Create and validate a program.
  list    List programs.
  show    Show a program.
  update  Update and validate a program.
  edit    Edit a program in the default editor.
  delete  Delete a program record.
```

```text
Usage: hiraku program create [OPTIONS] PROGRAM_ID

Create and validate a program.

Options:
  --markdown-file PATH  Read the Markdown body from PATH.
  --set KEY=VALUE       Set an additional field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku program list [OPTIONS]

List programs.

Options:
  --json  Emit complete program records as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku program show [OPTIONS] PROGRAM_ID

Show a program.

Options:
  --json  Emit the complete program record as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku program update [OPTIONS] PROGRAM_ID

Update and validate a program.

Options:
  --markdown-file PATH  Replace the Markdown body from PATH.
  --set KEY=VALUE       Set a field; repeatable.
  --unset KEY           Remove a field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku program edit PROGRAM_ID

Edit a program record in the default editor.
```

```text
Usage: hiraku program delete PROGRAM_ID

Delete a program record.
```

### Notes

Default location is
`hiraku/programs/{program_id}/program.md`. The core CLI manages records only;
it does not execute or activate programs. `default_program` belongs to the
agent layer.

### Examples

```bash
hiraku program create my-program
hiraku program update my-program --set time_budget=2h
```

## Actions

```text
Usage: hiraku action COMMAND [ARGS...]

Manage entity-scoped action records.

Commands:
  create  Create and validate an action.
  list    List actions.
  show    Show an action.
  update  Update and validate an action.
  edit    Edit an action in the default editor.
  delete  Delete an action record.
```

`ACTION` has the form `{entity}/{action_id}`.

```text
Usage: hiraku action create [OPTIONS] ACTION

Create and validate an entity-scoped action.

Options:
  --markdown-file PATH  Read the Markdown body from PATH.
  --set KEY=VALUE       Set an additional field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku action list [OPTIONS]

List actions.

Options:
  --entity ENTITY  Restrict results to one entity.
  --json           Emit complete action records as JSON.
  --help           Show this message and exit.
```

```text
Usage: hiraku action show [OPTIONS] ACTION

Show an action.

Options:
  --json  Emit the complete action record as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku action update [OPTIONS] ACTION

Update and validate an action.

Options:
  --markdown-file PATH  Replace the Markdown body from PATH.
  --set KEY=VALUE       Set a field; repeatable.
  --unset KEY           Remove a field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku action edit ACTION

Edit an action record in the default editor.
```

```text
Usage: hiraku action delete ACTION

Delete an action record.
```

### Notes

Default location is
`hiraku/actions/{entity}/{action_id}/action.md`. The core CLI does not invoke
or activate actions.

### Examples

```bash
hiraku action create experiment/review
hiraku action update experiment/review --set requires_commit=true
```

## Contexts

```text
Usage: hiraku context COMMAND [ARGS...]

Manage context records.

Commands:
  create  Create and validate a context.
  list    List contexts.
  show    Show a context.
  update  Update and validate a context.
  edit    Edit a context in the default editor.
  delete  Delete a context record.
```

```text
Usage: hiraku context create [OPTIONS] CONTEXT_ID

Create and validate a context.

Options:
  --markdown-file PATH  Read the Markdown body from PATH.
  --set KEY=VALUE       Set an additional field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku context list [OPTIONS]

List contexts.

Options:
  --json  Emit complete context records as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku context show [OPTIONS] CONTEXT_ID

Show a context.

Options:
  --json  Emit the complete context record as JSON.
  --help  Show this message and exit.
```

```text
Usage: hiraku context update [OPTIONS] CONTEXT_ID

Update and validate a context.

Options:
  --markdown-file PATH  Replace the Markdown body from PATH.
  --set KEY=VALUE       Set a field; repeatable.
  --unset KEY           Remove a field; repeatable.
  --help                Show this message and exit.
```

```text
Usage: hiraku context edit CONTEXT_ID

Edit a context record in the default editor.
```

```text
Usage: hiraku context delete CONTEXT_ID

Delete a context record.
```

### Notes

Default location is
`hiraku/contexts/{context_id}/context.md`. The core CLI does not compose or
activate contexts.

### Examples

```bash
hiraku context create literature --markdown-file literature.md
hiraku context show literature
```

## Launching entrypoints

```text
Usage: hiraku launch [HIRAKU_OPTIONS] TARGET [ENTRYPOINT_OVERRIDES...]

Resolve and launch a task or experiment entrypoint.

Arguments:
  TARGET                Entrypoint name, or
                        {experiment_id}:{entrypoint}.
  ENTRYPOINT_OVERRIDES  Arguments applied to the entrypoint declaration.

Options:
  --task TASK_ID              Select the task used to resolve TARGET; default
                              to the active task.
  --experiment EXPERIMENT_ID  Attribute a task entrypoint to an experiment.
  --no-experiment             Suppress implicit experiment attribution.
  --unassigned                Launch without task or experiment attribution.
  --commit REF                Launch from REF in an isolated worktree.
  --dispatch NAME             Dispatcher name [default: launch].
  --help                      Show this message and exit.
```

### Notes

`TARGET` terminates Hiraku option parsing. All Hiraku options precede it; every
later token is an entrypoint override. A post-target `--commit`, `--task`, or
`--dispatch` therefore belongs to the entrypoint.

An unqualified target selects a task declaration. An experiment target selects
from that experiment within the selected task. Namespaces never merge or fall
back. The CLI passes the resolved `Entrypoint`, its name, and explicit
attribution to `hiraku.launch.launch()`; that function performs no active-state
resolution.

By default, a task target receives task attribution and a compatible active
experiment. An experiment target receives both IDs. `--experiment` attributes
a task entrypoint without changing its declaration source or active state.
`--no-experiment` retains task attribution only. `--unassigned` suppresses both
IDs after using selected records to find the declaration, and conflicts with
`--experiment` and `--no-experiment`.

Without a commit, execution uses the current working tree. `--commit` uses an
isolated temporary worktree. A bound attributed experiment supplies its commit
when absent; an explicit commit must match it. Launching an unbound proposal
does not bind it.

Overrides use `--key value` for scalars, repeated flags for sequences, and a
valueless flag for true. They replace defaults. Rendering converts underscores
to hyphens, repeats sequences, emits true booleans as flags, and omits false or
null values.

The dispatcher resolves to `hiraku/dispatch/{name}.sh` under default
configuration. It receives the rendered command after `--`; synchronous launch
returns the dispatch process result. Hiraku stores no launch record and exposes
no scheduler-specific result.

Applicable values are injected as `HIRAKU_TASK_ID`, `HIRAKU_TASK_FILE`,
`HIRAKU_EXPERIMENT_ID`, `HIRAKU_EXPERIMENT_FILE`, `HIRAKU_COMMIT`, and
`HIRAKU_ENTRYPOINT`. Unassigned launches omit task and experiment variables.
Caller additions cannot override injected values.

### Examples

```bash
hiraku launch evaluate
hiraku launch logistic-baseline:train --seed 7
hiraku launch --commit HEAD logistic-baseline:train --seed 7
```

Here `--commit HEAD` is instead an entrypoint override because it follows the
target:

```bash
hiraku launch logistic-baseline:train --commit HEAD
```

## Runs

```text
Usage: hiraku run COMMAND [ARGS...]

Query runs from the configured tracking backend.

Commands:
  list  List normalized runs for a task.
```

```text
Usage: hiraku run list [OPTIONS]

List normalized runs for a task.

Options:
  --task TASK_ID              Select a task; default to the active task.
  --experiment EXPERIMENT_ID  Filter by an experiment in the selected task.
  --metrics NAME[,NAME...]    Select metric columns.
  --params NAME[,NAME...]     Select parameter columns.
  --sort NAME                 Sort by a metric.
  --ascending                 Sort in ascending order.
  --descending                Sort in descending order.
  --limit INTEGER             Limit the number of returned runs.
  --json                      Emit complete normalized Run objects as JSON.
  --help                      Show this message and exit.
```

### Notes

The active experiment is not an implicit filter. Cross-task listing is outside
the PoC. `--ascending` and `--descending` conflict and require `--sort`.

Task-level `tracking_backend` overrides the project default. CSV is default and
MLflow is optional. Both normalize results to `Run` objects. Human tables show
run ID, optional experiment, timestamp, commit, selected parameters, and
metrics; human IDs may be abbreviated.

Experiment `Metric` declarations replace task declarations; absent experiment
metrics inherit the task list. Declarations determine default columns and sort
direction. Explicit options override them. Missing or non-numeric values remain
after ranked rows. Without explicit sorting, core timestamp-then-ID ordering is
retained. CSV values remain strings except timestamps; MLflow preserves
normalized native values. Parameters and raw fields are never inferred as
metrics. Run commands query only; they do not log, mutate, or delete runs.

### Examples

```bash
hiraku run list
hiraku run list --experiment logistic-baseline --metrics val/log_loss,val/accuracy
hiraku run list --sort val/log_loss --ascending --limit 20
```

## Deferred scope

The deterministic core CLI excludes agent, action, and program execution;
context composition; active context sets; autonomous loops; lifecycle
enforcement; dashboards; cross-task run queries; backend mutation; run
deletion; and universal logging. These capabilities may be layered on the
public Python API without changing its record or launch semantics.

## Conformance

Generated help for the top level, every entity group and subcommand, `init`,
`status`, `launch`, and run listing should have stable snapshot tests. Parser
tests must verify every example, active-state fallback, repeatable option,
mutually exclusive option, and the launch target boundary. Snapshot formatting
may vary only in the presentation details declared implementation-defined at
the beginning of this document.
