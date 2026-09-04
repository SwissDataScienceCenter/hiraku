# Python Core API

The Hiraku Python API is the authoritative programmatic interface to Hiraku's
project metadata and model-development workflow. The `hiraku` command-line
interface and future user interfaces, such as dashboards and TUIs, are clients
of this API; they must not introduce data-model or lifecycle semantics of their
own.

The core API is deliberately small. Hiraku owns task and experiment records,
resolves and launches named entrypoints, and queries runs through a tracking
backend. It does not replace Git, execute model-specific training logic, or own
the run records stored by MLflow, CSV, or another backend.

This document specifies the intended public API. It is normative for new core
implementations, although Hiraku itself is still a draft and some interfaces
may change before the first stable release.

## Installation and imports

The principal data types are available from `hiraku.data`:

```python
from hiraku.data import (
    Entrypoint,
    Experiment,
    Metric,
    Run,
    Task,
    list_experiments,
    list_tasks,
    load_experiment,
    load_task,
)
```

Supporting interfaces live in focused modules:

```python
from hiraku.config import HirakuConfig
from hiraku.launch import launch, render_arguments
from hiraku.records import Record
from hiraku.tracking import TrackingBackend
```

Applications should import documented public names rather than internal file
helpers. Filesystem layout, YAML parsing, Git commands, and backend-specific
normalization are implementation details.

### Implementation baseline

The first core implementation is a greenfield Python 3.11+ package using a
`src/hiraku/` layout, `pyproject.toml`, `uv`, hatchling, pytest, Ruff, and
PyYAML. MLflow integration is an optional extra and imports its SDK lazily.
Prototype wheels produced before this specification are not compatibility
targets.

The implementation is expected to be clean, idiomatic, fully typed, and
documented according to Python library best practices. It will receive detailed
human review; correctness, maintainability, safe filesystem and Git behavior,
and clear public interfaces take precedence over shortcuts.

## Design principles

The API follows these rules:

- **The working directory is the current view.** Reads reflect the metadata
  visible in the current checkout.
- **Records are lightweight.** Hiraku validates only fields whose meaning it
  owns and preserves arbitrary user-defined fields.
- **References are explicit.** Experiments contain a task ID and may contain a
  source commit. Runs contain task, experiment, and commit attribution when the
  backend provides it.
- **A proposal is an experiment without a commit.** It is not a separate Python
  record type.
- **Runs are backend-owned.** `Run` is a normalized, read-only view of an
  execution trace, not a file managed by the Hiraku record registry.
- **Launching is deterministic.** Hiraku resolves configuration, constructs a
  command and environment, then delegates execution to a dispatch script.
- **Git remains independent.** Hiraku may inspect commits and branches, but
  committing, merging, conflict resolution, and preservation of experiment
  branches remain the user's or agent's responsibility.

## Project configuration

### `HirakuConfig`

```python
class HirakuConfig:
    @classmethod
    def discover(cls, start: Path | str | None = None) -> HirakuConfig: ...

    @classmethod
    def init(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
    ) -> HirakuConfig: ...
```

`HirakuConfig` represents a Hiraku project and its resolved configuration.
`discover()` searches from `start`, or the current directory, toward the
filesystem root for the fixed marker `hiraku.yml`. If `start` names a file, the
search begins in its parent directory. The directory containing the marker is
the project root. `discover()` raises `ProjectNotFoundError` if it reaches the
filesystem root without finding the marker.

`init()` creates `hiraku.yml`, the default metadata directories, editable
default programs, actions, and dispatch scripts, and the repository-local
`/hiraku` skill when `install_skill=True`. It leaves the active task and
experiment unset and does not create either record. It raises
`ProjectExistsError` if `hiraku.yml` already exists and never merges with or
overwrites existing project configuration.

The configuration exposes resolved paths and defaults rather than requiring
callers to assemble paths themselves:

```python
config.root
config.path
config.hiraku_dir
config.tasks_dir
config.programs_dir
config.actions_dir
config.contexts_dir
config.dispatch_dir
config.tracking_backend
```

`config.path` is always `<root>/hiraku.yml` and is not configurable.
`config.hiraku_dir` is the only configurable metadata location and defaults to
`<root>/hiraku`. The task, program, action, context, and dispatch directories
are derived from it and are not independent configuration keys. All resolved
paths are absolute `pathlib.Path` objects. A relative `hiraku_dir` is resolved
against `config.root` and must remain within it.

Record paths follow a fixed Markdown layout beneath the derived directories:

```text
hiraku/tasks/{task_id}/task.md
hiraku/tasks/{task_id}/experiments/{experiment_id}.md
hiraku/programs/{program_id}/program.md
hiraku/actions/{entity}/{action_id}/action.md
hiraku/contexts/{context_id}/context.md
```

The remaining PoC defaults are:

```yaml
hiraku_dir: hiraku
default_dispatch: launch.sh
task_branch: hiraku/tasks/{task_id}
experiment_branch: hiraku/experiments/{task_id}/{experiment_id}
default_program: interactive
tracking_backend:
  type: csv
```

CSV is the project tracking default. MLflow is the supported alternative in the
PoC. A task-level `tracking_backend` field overrides the project default and
uses the same mapping shape as the project setting.

The configuration also stores the active task and experiment:

```python
config.active_task_id
config.active_experiment_id

config.set_active_task("heart-disease")
config.set_active_experiment("logistic-baseline")
config.clear_active_experiment()
```

At most one task and one experiment are active. An active experiment always
belongs to the active task. Setting an experiment activates its owning task;
switching to a different task clears the active experiment. Setting either
value validates that the referenced record exists.

Project-aware helpers use `HirakuConfig.discover()` when `config` is omitted.
An explicit configuration may be passed for isolated tests or applications
that work with more than one project. Direct record loading, listing, and
saving use explicit paths and never discover configuration.

## File-backed records

### `Record`

```python
class Record(BaseModel):
    id: str
    path: Path | None
    markdown: str

    @classmethod
    def load(cls, path: str | Path) -> Self: ...

    @classmethod
    def list(cls, paths: Iterable[Path]) -> list[Self]: ...

    def save(
        self,
        path: str | Path | None = None,
        *,
        overwrite: bool = False,
    ) -> Path: ...
    def delete(self) -> None: ...
    def to_dict(self) -> dict[str, Any]: ...
```

`Record` is the shared Pydantic base class for Hiraku-owned records. A
record separates structured fields from optional prose:

- Markdown files store structured fields in YAML front matter and expose the
  remaining text through `markdown`; the body may be empty.
- Unknown fields are preserved across a load/edit/save round trip.

Constructing a record creates a detached in-memory object. It neither writes a
file nor changes active project state. The first `save(path)` establishes its
explicit `.md` location and serializes the structured fields as YAML front
matter followed by `markdown`. Later `save()` calls reuse that path. Passing a
different path after persistence is rejected rather than moving the record.

Declared fields are accessed as validated attributes:

```python
task.goal
task.summary
task.metrics
```

Unknown extension fields are available through `record.model_extra` and are
preserved across persistence. Pydantic validates declared fields at model
construction and assignment time.

`load()` accepts one explicit path. `list()` accepts an iterable of paths, such
as `directory.glob("*/task.md")`, and returns records ordered by ID so results
are deterministic. These operations do not discover a project. Project-aware
helpers such as `load_task()` and `list_tasks()` resolve canonical registry
paths from configuration. `save()` validates before writing and returns the
written path. `delete()` removes only the bound record represented by the
object; subclasses may reject deletion when dependent records exist.

Record IDs are stable, filesystem-safe identifiers. They must be non-empty and
must not contain path separators, `.` or `..` path components, or control
characters. Renaming an ID is a separate lifecycle operation because references
may need updating.

## Tasks

### `Metric`

```python
class Metric(BaseModel):
    name: str
    direction: Literal["minimize", "maximize"]
```

Both keys are required. Metric names remain backend-defined; Hiraku interprets
only the ordering direction. `Metric` is a declaration; observed values are
stored separately in `Run.metrics`.

### `Task`

```python
class Task(Record):
    id: str
    branch: str | None
    goal: str | None
    question: str | None
    summary: str | None
    entrypoints: Mapping[str, Entrypoint]
    metrics: Sequence[Metric]
    tracking_backend: str | Mapping[str, Any] | None
    mlflow_experiment: str | None

    @classmethod
    def create(
        cls,
        task_id: str,
        *,
        markdown: str = "",
        activate: bool = True,
        config: HirakuConfig | None = None,
        **fields: Any,
    ) -> Task: ...

    def experiments(
        self,
        *,
        config: HirakuConfig | None = None,
    ) -> list[Experiment]: ...
    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]: ...
    def entrypoint(self, name: str) -> Entrypoint: ...

def load_task(task_id: str, *, config=None) -> Task: ...
def list_tasks(*, config=None) -> list[Task]: ...
```

A task is the top-level scope of a research effort. Its canonical path is:

```text
hiraku/tasks/{task_id}/task.md
```

The only required core field is `id`. `branch`, `entrypoints`, `metrics`, and
`tracking_backend` are optional semantic fields. `goal`, `question`, and
`summary` are explicit optional Task fields and are validated when supplied.

```yaml
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
```

`Task(...)` constructs a detached task; the caller must invoke `save()` to
persist it. `Task.create()` is a convenience that constructs, validates, and
saves a task atomically. By default it also makes the task active and clears
any previously active experiment. It raises `RecordExistsError` instead of
replacing an existing task. Neither construction nor plain `save()` activates
the task.

```python
# An empty body is still a Markdown record with YAML front matter.
diagnostic = Task(
    id="data-diagnostic",
    markdown="",
    owner="research-team",  # custom metadata in model_extra
)
diagnostic.save(config.tasks_dir / "data-diagnostic" / "task.md")

baseline = Task.create("baseline", markdown="", goal="Establish a baseline")
```

`experiments()` lists the task's experiment records. `runs()` delegates to the
configured tracking backend and restricts results to the task. Neither method
changes project state.

Deleting a task with experiments is rejected unless an explicit recursive
operation is requested by a higher-level interface. Core code must not silently
delete backend-owned runs or Git branches.

## Experiments and proposals

### `Experiment`

```python
class Experiment(Record):
    id: str
    task_id: str
    summary: str | None
    hypothesis: str | None
    commit: str | None
    branch: str | None
    entrypoints: Mapping[str, Entrypoint]
    metrics: Sequence[Metric] | None

    @property
    def is_proposal(self) -> bool: ...

    @classmethod
    def create(
        cls,
        experiment_id: str,
        *,
        task: Task | str | None = None,
        commit: str | None = None,
        markdown: str = "",
        activate: bool = True,
        config: HirakuConfig | None = None,
        **fields: Any,
    ) -> Experiment: ...

    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]: ...
    def entrypoint(self, name: str) -> Entrypoint: ...

def load_experiment(
    reference: str,
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> Experiment: ...

def list_experiments(
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> list[Experiment]: ...
```

An experiment is a focused investigation within exactly one task. Its canonical
path is:

```text
hiraku/tasks/{task_id}/experiments/{experiment_id}.md
```

The required core fields are `id` and `task_id`. `commit`, `branch`,
`entrypoints`, and `metrics` are optional semantic fields. `summary` and
`hypothesis` are conventional user fields. A missing or null `commit` means the
experiment is a proposal; there is no separate proposal record type.

`commit` is passive metadata. Experiment construction, loading, creation, and
saving never invoke Git or resolve symbolic references. Core validation accepts
null or a full lowercase Git object ID: 40 hexadecimal characters for SHA-1 or
64 for SHA-256. It does not check that the commit exists or belongs to a
repository. Callers that bind an experiment should supply the stable commit
identifier they intend to persist. In particular, Python callers may explicitly
use `GitRepository.resolve()` before assigning a user-facing ref such as `HEAD`.

An experiment ID is unique within its task, not necessarily across the whole
project. `load_experiment()` therefore accepts either a task scope or a
qualified identifier of the form `{task_id}:{experiment_id}`. An unqualified
ID uses the active task. `Experiment.load(path)` remains the configuration-free
file operation. Ambiguous or missing project scope raises
`AmbiguousReferenceError` or `NoActiveTaskError` rather than guessing.

To turn a proposal into a bound experiment, callers update `commit` through
normal attribute assignment and call `save()`. Record persistence does
not verify that the identifier exists in the current repository. `runs()`
returns backend runs attributed to both its task and experiment.

`Experiment(...)` creates a detached record and `save()` persists it without
changing active state. `Experiment.create()` constructs, validates, and saves
the record. With its default `activate=True`, it also activates the experiment
and its owning task. Selecting an existing experiment follows the same active
task rule; switching to another task clears an incompatible active experiment.

An experiment's `metrics` replace the task's metric list for that experiment.
When experiment metrics are absent, run presentation inherits the owning
task's metrics. The `entrypoints` mapping is the listing interface;
`entrypoint(name)` resolves one entrypoint and raises `EntrypointNotFoundError`
when it is absent.

Experiment records have no mandatory scientific semantics beyond linking the
task, code, entrypoints, and runs. Users may use them for baselines,
hypotheses, diagnostics, ablations, hyperparameter searches, or other focused
investigations.

### Branches and lineage

`branch` records the optional Git branch used to implement an experiment. It is
metadata, not ownership: saving or deleting an experiment does not create or
delete a branch unless a branch-specific method is explicitly invoked.

Experiment ancestry is derived from the Git history of bound commits. It is not
stored as a required `parent_id` field. Consequently, proposals do not yet have
Git-derived lineage. APIs may expose lineage helpers, but callers must tolerate
an empty or incomplete result for unbound experiments, identifiers from another
repository, and unreachable commits.

Git operations are implemented through a dedicated service. It resolves
commits, inspects and creates branches, switches branches when explicitly
requested, derives lineage, and manages temporary worktrees for launches.
Hiraku does not automatically commit, merge, reset, delete branches, or resolve
conflicts. Those operations remain the user's or agent's responsibility.

## Runs

### `Run`

```python
class Run(BaseModel):
    id: str
    metrics: Mapping[str, Any]
    params: Mapping[str, Any]
    task_id: str | None = None
    experiment_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    entrypoint: str | None = None
    url: str | None = None
    raw: Mapping[str, Any] = Field(default_factory=dict, repr=False)
```

`Run` is an immutable normalized projection of a backend-owned execution trace.
Hiraku does not create a per-run metadata file. The `raw` mapping may retain
backend-specific values that do not fit the portable fields.

Every run has an `id`, `metrics`, and `params`. Task, experiment, commit,
timestamp, entrypoint, and URL attribution are optional because a backend may
not provide them. Missing values remain `None`; Hiraku does not infer
attribution from temporal proximity or other runs.

`Run` does not resolve tasks or experiments and has no query method. Querying
belongs to `Task.runs()` and `Experiment.runs()`, which pass their own explicit
identifiers to the selected backend. At the backend boundary, `task_id` is
required because backend selection and portable CSV storage are task-scoped.
An omitted `experiment_id` means that no experiment filter is applied. Neither
case falls back to active project selections.

Run mutation is absent from the core interface. Model code logs through the
tracking system's native client; Hiraku only queries normalized runs.

## Tracking backends

### `TrackingBackend`

```python
class TrackingBackend(Protocol):
    def list_runs(
        self,
        *,
        task_id: str,
        experiment_id: str | None = None,
        **query: Any,
    ) -> list[Run]: ...
```

A tracking backend is a compatibility adapter for reading runs. The PoC ships
adapters for CSV and MLflow. Each adapter normalizes its backend-specific
results and returns a list of `Run` objects.

Backend selection is task-scoped. A task-specific setting overrides the project
default. Credentials and remote-service configuration should remain in the
backend's conventional environment or configuration; they should not be copied
into task records.

The read-only adapters use these mappings:

- CSV defaults to `hiraku/tasks/{task_id}/runs.csv`. Standard columns are
  `id`, `task_id`, `experiment_id`, `commit`, `timestamp`, `entrypoint`, and
  `url`; `metric.<name>` and `param.<name>` columns populate metrics and
  parameters, and unknown columns are preserved in `raw`.
- MLflow maps its run ID to `Run.id`, `hiraku.task_id`,
  `hiraku.experiment_id`, `hiraku.commit`, and `hiraku.entrypoint` tags to
  attribution, and native metrics and parameters to their normalized mappings.
Missing optional attribution remains `None`. The MLflow SDK is imported only
when its adapter is selected; a missing extra raises `BackendError`.


## Entrypoints and launching

### `Entrypoint`

Tasks and experiments may define named entrypoints in front matter:

```yaml
entrypoints:
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      model: logistic
      seed: 42
```

`command` is an argument vector, not a shell string. `args` contains default
named arguments. Using an argument vector prevents shell-dependent parsing and
makes the rendered invocation inspectable.

Entrypoints are validated Pydantic models stored on their owning record:

```python
ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]

class Entrypoint(BaseModel):
    command: list[str]
    args: dict[str, ArgumentValue] = Field(default_factory=dict)
```

Argument mappings use GNU-style rendering. Keys become `--key`, with
underscores converted to hyphens. Scalar values follow the flag, `True` emits
the flag alone, `False` and `None` are omitted, and sequences repeat the flag
for each item. Call-time values override stored defaults. Nested mappings and
unsupported values raise `ValidationError`.

Experiment entrypoints are resolved on the experiment record. Task entrypoints
are resolved on the task record. Hiraku does not implicitly merge two
definitions with the same name.

### `hiraku.launch`

```python
def render_arguments(arguments: Mapping[str, ArgumentValue]) -> tuple[str, ...]: ...

def launch(
    entrypoint: Entrypoint,
    *,
    name: str | None = None,
    args: Mapping[str, ArgumentValue] | None = None,
    task_id: str | None = None,
    experiment_id: str | None = None,
    commit: str | None = None,
    dispatch: str | None = None,
    worktree: bool | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    config: HirakuConfig | None = None,
) -> subprocess.CompletedProcess[str]: ...
```

`hiraku.launch` is the sole public execution API. Tasks and experiments expose
entrypoint declarations but do not implement launch methods. Call-time
arguments override entrypoint defaults without mutating the declaration.
`name`, `task_id`, and `experiment_id` are explicit attribution only. `None`
does not consult active configuration: the corresponding environment variable
and file variable are omitted. A launch with both IDs absent is unassigned.
An explicit task must exist. Because experiment IDs are task-scoped,
`experiment_id` requires `task_id` and must resolve within that task.

When `commit` is supplied, Hiraku resolves it and launches from an isolated Git
worktree. When it is absent, Hiraku launches from the current working tree.
Supplying IDs does not implicitly select a commit or change active state.

The launch environment includes at least:

```text
HIRAKU_TASK_ID
HIRAKU_TASK_FILE
HIRAKU_EXPERIMENT_ID
HIRAKU_EXPERIMENT_FILE
HIRAKU_COMMIT
HIRAKU_ENTRYPOINT
```

Only explicitly supplied attribution variables are injected. Values supplied
via `env` may add variables but must not override injected `HIRAKU_*` values.

The selected dispatch script receives the complete rendered command after a
`--` separator. The project default is `hiraku/dispatch/launch.sh`; a named
dispatcher such as `runai` resolves to `hiraku/dispatch/runai.sh`. Dispatchers
are user-managed integration points for environments and launch event logging.

For synchronous execution, `launch()` returns the underlying
`subprocess.CompletedProcess`. With `check=True`, a non-zero exit status raises
`LaunchError`. Dispatch scripts may submit work to another execution system,
but Hiraku exposes only the completed dispatch process. Hiraku does not persist
a launch record; only the tracking backend creates and owns runs.

## Agent metadata records

Programs, actions, and contexts use the same `Record` serialization contract,
but they belong to the agent-integration layer rather than the task/experiment/
run core model:

```python
from hiraku.agent import (
    Action,
    Context,
    Program,
    list_actions,
    list_contexts,
    list_programs,
    load_action,
    load_context,
    load_program,
)
```

- `Program` records use `hiraku/programs/{program_id}/program.md` and define
  workflow instructions, permissions, resource limits, checkpoints, and
  stopping conditions.
- `Action` records use
  `hiraku/actions/{entity}/{action_id}/action.md` and define a one-step agent
  operation plus deterministic preconditions and hooks.
- `Context` records use `hiraku/contexts/{context_id}/context.md` and contain
  curated material that can be referenced by tasks, actions, or programs.

The Python core may load and validate these records, but executing LLM
instructions is the responsibility of `hiraku-agent` or a compatible harness.
Deterministic lifecycle checks must remain callable independently of an LLM.
The first core milestone implements `Program`, `Action`, and `Context` only as
file-backed record types; agent, program, action, and context-composition
execution is deferred.

## Validation and errors

All public exceptions derive from `HirakuError`:

```python
from hiraku.errors import (
    AmbiguousReferenceError,
    BackendError,
    EntrypointNotFoundError,
    HirakuError,
    LaunchError,
    NoActiveExperimentError,
    NoActiveTaskError,
    ProjectExistsError,
    ProjectNotFoundError,
    RecordExistsError,
    RecordNotFoundError,
    ValidationError,
)
```

Expected error conditions use typed exceptions rather than process exits.
Command-line clients translate these exceptions into messages and exit codes.
`BackendError` reports a failure while querying a tracking backend or
normalizing its results into `Run` objects.

Validation covers framework semantics, including:

- required core fields and their types;
- safe, internally consistent IDs;
- task references on experiments;
- unique record IDs within each registry;
- valid entrypoint structure;
- active experiment/task consistency; and
- resolvable commits when a Git-backed operation, such as ref resolution,
  lineage inspection, or a committed launch, requires one. Record persistence
  does not perform this check.

Hiraku does not prescribe or validate user-defined scientific fields such as
`hypothesis`, `result`, `dataset`, or metric names beyond basic serialization.
Actions and programs may impose additional workflow-specific schemas without
turning those fields into universal core fields.

## End-to-end example

```python
from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Run, Task

config = HirakuConfig.init(".")

task = Task.create(
    "heart-disease",
    markdown="",
    goal="Develop a well-calibrated heart-disease predictor.",
    question="Which model gives the best validation log loss?",
    tracking_backend={
        "type": "mlflow",
        "database": "mlflow.db",
    },
    metrics=[
        {"name": "val/log_loss", "direction": "minimize"},
        {"name": "val/accuracy", "direction": "maximize"},
    ],
    entrypoints={
        "evaluate": {
            "command": ["uv", "run", "python", "evaluate.py"],
        }
    },
    config=config,
)

experiment = Experiment.create(
    "logistic-baseline",
    task=task,
    summary="Regularized logistic-regression baseline",
    hypothesis="Regularization will improve calibration.",
    # This experiment-specific list replaces the task metric defaults.
    metrics=[{"name": "val/log_loss", "direction": "minimize"}],
    entrypoints={
        "train": {
            "command": ["uv", "run", "python", "train.py"],
            "args": {"model": "logistic", "seed": 42},
        }
    },
    config=config,
)

# Implementation and Git commit happen through normal development tools.
from hiraku.git import GitRepository

experiment.commit = GitRepository(config).resolve("HEAD")
experiment.save()  # stores the supplied identifier without invoking Git

from hiraku.launch import launch

completed = launch(
    experiment.entrypoint("train"),
    name="train",
    task_id=experiment.task_id,
    experiment_id=experiment.id,
    commit=experiment.commit,
    args={"seed": 1},
)
if completed.returncode == 0:
    runs = experiment.runs(sort="val/log_loss", ascending=True)
    best = runs[0] if runs else None
```

This sequence preserves the separation of responsibilities: Hiraku owns the
task and experiment metadata, Git identifies the implementation, the
entrypoint and dispatcher execute it, and the selected tracking backend owns
and returns the resulting runs.

## Stability and open design points

The following areas remain draft and should not be treated as stable extension
points yet:

- lifecycle statuses and state transitions for experiments;
- explicit context references and context composition;
- program execution and harness enforcement;
- the portable query language for tracking backends; and
- whether proposal lineage needs an explicit parent relationship in addition
  to Git-derived lineage.

Until these are settled, implementations should keep them behind narrow
interfaces and preserve unknown record fields so future schema additions do not
discard user metadata.
