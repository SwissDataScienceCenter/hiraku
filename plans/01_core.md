# Plan 01: Hiraku Core Python API

## Objective

Implement the first production-quality Hiraku Python package from the contracts
in `README.md`, `docs/install.md`, and `docs/core.md`. The milestone covers
configuration, file-backed metadata records, tasks, experiments, normalized
runs, entrypoints and launching, Git/worktree operations, and read-only CSV,
and MLflow tracking adapters.

The implementation is greenfield. Ignored prototype wheels and scratch projects
are not compatibility targets. CLI commands, dashboards, `hiraku-agent`, model
providers, program execution, context composition, and additional tracking
adapters are later milestones.

This code will receive detailed human review. It must be clean, idiomatic,
fully typed, documented, easy to test, and conservative around filesystem, Git,
and subprocess operations. Passing tests and linting are baseline acceptance
requirements, not cleanup work.

Keep README, core, and installation contracts unchanged unless implementation reveals
a direct contradiction. Any newly discovered high-level conflict must be
resolved with the project owner before changing behavior.

## Package foundation

Create a Python 3.11+ project using:

- `pyproject.toml` with hatchling and a `src/hiraku/` package layout;
- `uv` for environment and dependency management;
- PyYAML as the core runtime dependency;
- an optional `mlflow` dependency group;
- pytest for tests and Ruff for linting and formatting; and
- package data for default programs, actions, dispatch scripts, and the
  repository-local `/hiraku` skill.

Organize the public implementation into these modules:

```text
hiraku.config       HirakuConfig and project initialization
hiraku.io           YAML and atomic file I/O helpers
hiraku.records      Record and Markdown serialization
hiraku.data         Task, Experiment, Run, Metric, Entrypoint
hiraku.launch       Entrypoint validation, argument rendering, launching
hiraku.git          GitRepository and temporary worktrees
hiraku.tracking     Backend protocol, selection, CSV/MLflow adapters
hiraku.agent        Program, Action, Context record types only
hiraku.errors       Public exception hierarchy
```

Use descriptive public module names throughout the implementation. Do not add
underscore-prefixed private modules; keep implementation details private through
the documented export surface and naming within their owning public module.

Re-export only documented names. Keep path expansion, YAML/front-matter
parsing, backend normalization, Git commands, and subprocess construction
internal.

## Error model

All public errors derive from `HirakuError`. Implement:

- `ProjectNotFoundError` and `ProjectExistsError`;
- `RecordNotFoundError` and `RecordExistsError`;
- `ValidationError` and `AmbiguousReferenceError`;
- `NoActiveTaskError` and `NoActiveExperimentError`;
- `EntrypointNotFoundError`;
- `GitError`, `LaunchError`, and `BackendError`.

Library functions raise exceptions and never terminate the process. Messages
must identify the relevant path, record, ref, entrypoint, or backend. Preserve
the original exception as the cause when wrapping filesystem, Git, SDK, or
subprocess failures.

## Configuration and project discovery

### Discovery

Implement `HirakuConfig.discover(start=None)` as follows:

1. Resolve `start`, defaulting to the current working directory.
2. If it identifies a file, begin from its parent.
3. Search that directory and its parents for the fixed marker `hiraku.yml`.
4. Set `path` to the marker and `root` to its parent.
5. Raise `ProjectNotFoundError` after reaching the filesystem root.

The marker location is not configurable. `hiraku_dir` is the only configurable
metadata location and defaults to `<root>/hiraku`; record paths use a fixed
layout beneath it.

### Initialization

Implement:

```python
@classmethod
def init(
    cls,
    root: Path | str = ".",
    *,
    install_skill: bool = True,
) -> HirakuConfig: ...
```

Initialization must:

- fail with `ProjectExistsError` when `hiraku.yml` exists;
- create the configuration atomically;
- create task, program, action, context, and dispatch directories;
- copy editable `interactive` and `autoresearch` programs;
- copy the documented default action records;
- copy an executable default `launch.sh` dispatcher;
- install the repository-local `/hiraku` skill when enabled;
- leave active task and experiment IDs unset; and
- create no task or experiment.

Package resources are copied byte-for-byte. Partial initialization failures
must remove only files created during that attempt and must never remove
pre-existing user files.

### Configuration schema

Support these defaults:

```yaml
hiraku_dir: hiraku
default_dispatch: launch.sh
task_branch: hiraku/tasks/{task_id}
experiment_branch: hiraku/experiment/{task_id}/{experiment_id}
default_program: interactive
tracking_backend: csv
active_task_id: null
active_experiment_id: null
```

Preserve unknown configuration keys when saving. Validate known value types.
Resolve `hiraku_dir` against the project root and reject lexical traversal or a
resolved symlink path outside the root. Derive `tasks_dir`, `programs_dir`,
`actions_dir`, `contexts_dir`, and `dispatch_dir` beneath `hiraku_dir`; they are
read-only properties rather than independent configuration keys.

### Active selection

Implement getters plus `set_active_task()`, `set_active_experiment()`, and
`clear_active_experiment()`.

- Selected records must exist.
- Setting an experiment activates its owning task.
- Switching to a different task clears an incompatible experiment.
- State changes validate first and write `hiraku.yml` atomically.

## Record storage

Implement `Record` as `MutableMapping[str, Any]` with detached construction and
the documented `load()`, `list()`, `save()`, `delete()`, `validate()`, and
`to_dict()` APIs.

### Identity and fields

- Store framework-owned fields and arbitrary user fields in one ordered mapping.
- Expose documented core fields as attributes.
- Access user fields through mapping methods.
- Validate IDs as non-empty, filesystem-safe identifiers without separators,
  traversal components, or control characters.
- Treat IDs as immutable after first persistence.

### Markdown records

- Every record uses its configured `.md` path.
- `markdown` is always a string and defaults to `""`.
- Markdown uses one YAML front-matter mapping followed by the body verbatim.
- Preserve unknown fields and insertion order across round trips.
- Reject malformed front matter, non-mapping front matter, and unsupported YAML
  values with typed errors.
- `save()` accepts no path or format override.

Write through a temporary sibling followed by atomic replacement. With
`overwrite=False`, reject a conflicting existing file not loaded as the same
record. Return the final path. `list()` must be deterministic and ID-sorted.

## Metrics and domain records

### Metric

Implement `Metric` as a `TypedDict` with required `name: str` and
`direction: Literal["minimize", "maximize"]`. Reject empty names, unknown keys
in the semantic shape, and invalid directions.

`Metric` declares how a metric is presented and ordered. Observed values remain
in `Run.metrics` and are not `Metric` objects.

### Entrypoint

Implement `Entrypoint` as a `TypedDict` in `hiraku.data`:

```python
ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]

class Entrypoint(TypedDict):
    command: Sequence[str]
    args: NotRequired[Mapping[str, ArgumentValue]]
```

Entrypoints are declarative values stored in the owning task or experiment.
They have no independent ID, owner state, or methods.

### Task

Core task fields:

- required: `id`;
- optional semantic: `branch`, `entrypoints`, `metrics`, `tracking_backend`;
- conventional user fields: `goal`, `question`, `summary`, and arbitrary
  additional metadata.

`Task(...)` creates a detached object. `Task.create()` constructs, validates,
saves, and activates by default. Construction and ordinary `save()` never
activate.

Implement:

- `experiments()` using the experiment registry;
- `runs()` using backend selection and task filtering;
- `entrypoint()` through the record data; launching is not a task method; and
- guarded deletion that fails while experiments exist and never deletes runs
  or branches.

### Experiment

Core experiment fields:

- required: `id`, `task_id`;
- optional semantic: `commit`, `branch`, `entrypoints`, `metrics`;
- conventional user fields: `summary`, `hypothesis`, and arbitrary metadata.

An experiment without `commit` is a proposal. Expose `is_proposal`; do not
create a separate proposal type or lifecycle status.

`Experiment.create()` resolves its task from an explicit object/ID or the
active task, saves, and activates by default. Experiment IDs are unique within
a task. Accept qualified `{task_id}:{experiment_id}` references. Reject missing
scope and genuine ambiguity instead of guessing.

When saving a non-null commit, resolve it through the Git service and persist
the full hash. To bind a proposal, callers set `commit` and call `save()`; do
not add `bind()`.

Experiment metrics replace task metrics when present and otherwise inherit
them. Implement `runs()` and `entrypoint()`; callers launch through
`hiraku.launch.launch()`.

### Agent metadata

Implement `Program`, `Action`, and `Context` as file-backed `Record` subclasses.
Validate IDs, action entity scope, and framework-owned structural fields only.
Preserve arbitrary front matter and Markdown.

Do not execute prompts, invoke models, compose context, enforce programs, or
implement action hooks in this milestone.

## Git service

Implement a dedicated `GitRepository` service using subprocess argument vectors
with no shell interpolation. It must:

- locate and validate the repository containing the project;
- resolve refs to full commit hashes;
- inspect the current branch and HEAD;
- validate branch names;
- inspect, create, and explicitly switch task or experiment branches;
- create experiment branches from an explicit ref or task-branch HEAD;
- derive parent/child experiment relationships from bound commit ancestry; and
- create and clean temporary worktrees for committed launches.

Temporary worktrees use a safe temporary parent, are removed after synchronous
dispatch on success or failure, and are pruned from Git metadata. Never perform
implicit commits, merges, resets, conflict resolution, branch deletion, or
metadata-record mutation.

Dirty working trees are allowed for working-tree launches but never copied into
a commit-based worktree. Operations that require Git raise `GitError` outside a
repository or for invalid/unresolvable refs.

## Public launch module

Implement entrypoint validation, argument rendering, dispatch resolution, and
execution in the public `hiraku.launch` module. Expose:

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

This is the sole public execution function. Tasks, experiments, and entrypoint
declarations do not have launch methods. It accepts the declaration directly;
all attribution is optional and explicit.

Validate that `command` is a non-empty sequence of strings and `args` is a
mapping. Task and experiment namespaces remain separate when records resolve an
entrypoint; do not merge matching names. Missing record entrypoints raise
`EntrypointNotFoundError` before `launch()` is called.

### Argument rendering

Merge defaults with call-time overrides without mutating the record, then:

- convert `key_name` to `--key-name`;
- append scalar strings, numbers, and path-like values after the flag;
- emit `True` as a valueless flag;
- omit `False` and `None`;
- repeat a flag for each sequence item; and
- reject nested mappings and unsupported values with `ValidationError`.

Preserve mapping and sequence order to keep commands deterministic.

### Launch behavior

- `name=None` omits `HIRAKU_ENTRYPOINT`.
- `task_id=None` and `experiment_id=None` omit their attribution and file
  variables; neither falls back to active configuration.
- A launch with both IDs absent is unassigned.
- An explicit task ID must resolve. An experiment ID requires a task ID and
  must resolve within that task so its file attribution is unambiguous.
- Explicit IDs never change active configuration or implicitly select a commit.
- A supplied commit launches from an isolated worktree; otherwise the launch
  uses the current working tree.
- Autonomous execution policies remain out of scope.

Inject `HIRAKU_TASK_ID`, `HIRAKU_TASK_FILE`, `HIRAKU_EXPERIMENT_ID`,
`HIRAKU_EXPERIMENT_FILE`, `HIRAKU_COMMIT`, and `HIRAKU_ENTRYPOINT` only when
their corresponding values are explicitly supplied. Caller environment
additions cannot replace injected attribution variables.

Resolve the default or named dispatcher inside `dispatch_dir`. Invoke it with
the rendered command after `--`. Return `subprocess.CompletedProcess[str]`.
Raise `LaunchError` on a non-zero status with `check=True`. A dispatcher may
submit remote work internally, but Hiraku exposes only the dispatch process and
does not persist launch records.

## Runs and tracking backends

Implement immutable `Run` values with:

- required `id`, `metrics`, and `params`;
- optional `task_id`, `experiment_id`, `commit`, `timestamp`, `entrypoint`, and
  `url`; and
- `raw` for unmapped backend data.

Never infer missing attribution. Normalize timestamps to timezone-aware UTC
datetimes when a backend supplies them.

`Run` is a normalized value only and must not resolve task or experiment
records or expose a registry-style query method. Run queries live on
`Task.runs()` and `Experiment.runs()`, which pass their own explicit identifiers
to the selected backend. `TrackingBackend.list_runs()` requires a non-empty
`task_id`; omitting `experiment_id` applies no experiment filter. Neither query
path falls back to active task or experiment configuration.

Define `TrackingBackend.list_runs()` to return `list[Run]`. Backend selection
uses an explicit instance for tests, otherwise the task-level configuration,
then the project default. Keep the adapters read-only.

### CSV

Default to `hiraku/tasks/{task_id}/runs.csv`.

- Standard columns: `id`, `task_id`, `experiment_id`, `commit`, `timestamp`,
  `entrypoint`, `url`.
- `metric.<name>` columns populate `metrics`.
- `param.<name>` columns populate `params`.
- Unknown columns populate `raw`.
- Require a non-empty `id`; metrics and params may be empty.
- Empty optional cells normalize to `None`.
- Report malformed rows with file and row number.

CSV values remain strings except for empty optional fields and parsed
timestamps; do not guess parameter or metric types in the portable layer.

### MLflow

Import MLflow lazily. Map:

- MLflow run ID to `Run.id`;
- `hiraku.task_id`, `hiraku.experiment_id`, `hiraku.commit`, and
  `hiraku.entrypoint` tags to attribution;
- MLflow metrics to `metrics`; and
- MLflow parameters to `params`.

Use native start time for `timestamp` and preserve relevant unmapped metadata
in `raw`. Query by task tag where possible, then apply portable filters.

For the optional MLflow adapter, missing dependencies, authentication failures,
query failures, and malformed results raise `BackendError` with the backend
name and original cause.

Sort normalized results deterministically by timestamp and then run ID. Runs
without timestamps sort after timestamped runs. Keep the generic query surface
limited to task/experiment filtering, ordering, and limits until a portable
query language is specified.


## Test plan

### Configuration and filesystem

- Discovery from project root, nested directory, and file path
- Discovery failure and duplicate initialization
- Complete initialization resources and skill opt-out
- Atomic configuration writes and preservation of unknown keys
- Active task/experiment invariants
- Derived directory paths, traversal, and symlink escapes

### Records and domain types

- Markdown round trips, including empty Markdown bodies
- Unknown field and ordering preservation
- Malformed YAML/front matter and non-mapping content
- Detached constructors versus activating `create()`
- Deterministic listings, overwrite conflicts, and guarded deletion
- Metric validation and experiment inheritance
- Proposal detection, commit resolution, scoped lookup, and ambiguity
- Program, action, and context paths and validation

### Git and launching

- Repository/ref/branch validation
- Task and experiment branch creation and explicit switching
- Commit ancestry and proposal exclusion
- Worktree isolation and cleanup on success and failure
- Dirty working-tree behavior
- GNU argument rendering for scalars, booleans, sequences, overrides, and
  rejected nested values
- Entrypoint namespace separation and missing names
- Attribution environment injection and override rejection
- Dispatcher resolution, return values, and failure wrapping

### Tracking

- CSV standard, metric, parameter, raw, empty, malformed, and timestamp fields
- Task/experiment backend precedence
- Deterministic ordering and missing attribution
- A real MLflow integration test using an isolated `tmp_path/mlflow.db` SQLite
  tracking store and the installed MLflow SDK; do not mock the primary adapter
  path
- Create an MLflow experiment and run, set the documented `hiraku.*` tags, log
  actual parameters and metrics, finish the run, query it through the Hiraku
  adapter, and assert all normalized `Run` fields
- Log at least one metric more than once and verify that the adapter returns
  MLflow's final/latest value
- Verify the real run ID, UTC-normalized start time, task and experiment
  filtering, commit and entrypoint attribution, parameters, metrics, and raw
  metadata
- Keep the MLflow integration test network-free by using an absolute
  `sqlite:///` URI under pytest's temporary directory; clean test state through
  temporary-directory teardown
- Lazy imports and missing-extra errors
- SDK query, authentication, and malformed-response errors

### Distribution and quality

- Public imports and documented signatures
- Package resources in wheels and source distributions
- Clean-wheel installation and initialization smoke test
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest`
- `uv build`, followed by installation into a clean environment

## Delivery sequence

1. Add packaging, quality configuration, exception types, and resource assets.
2. Implement discovery, initialization, configuration validation, and atomic IO.
3. Implement record serialization and registries.
4. Implement metrics, tasks, experiments, and agent metadata records.
5. Implement Git and temporary-worktree services.
6. Implement the public launch module, including entrypoint validation,
   rendering, dispatch, and cleanup.
7. Implement `Run`, backend selection, and CSV normalization.
8. Add the lazy MLflow adapter and validate it through actual MLflow smoke
   logging against a temporary local SQLite database.
9. Complete documentation examples and public docstrings.
10. Run linting, tests, builds, and clean-install smoke tests.
11. Review the entire diff for API consistency, unnecessary complexity,
    unsafe mutations, incomplete errors, and documentation drift.

## Definition of done

- Every in-scope public contract is implemented or explicitly marked as
  deferred above.
- All public APIs are typed and documented.
- Tests cover success, failure, and safety-sensitive behavior.
- Optional integrations do not affect core imports or installation.
- No operation silently discards metadata or performs destructive Git actions.
- Source and built distributions contain all initialization resources.
- Lint, format, test, build, and clean-install checks pass.
- `docs/core.md` accurately describes the delivered implementation.
- The code and documentation are ready for detailed human review.
