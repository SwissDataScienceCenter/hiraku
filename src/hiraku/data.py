"""Tasks, experiments, entrypoints, metrics, and normalized runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import HirakuConfig
from .errors import (
    AmbiguousReferenceError,
    EntrypointNotFoundError,
    NoActiveTaskError,
    RecordExistsError,
    ValidationError,
)
from .records import Record
from .tracking import TrackingBackend, select_backend
from .validation import validate_identifier

ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]
_LOWER_HEXADECIMAL = frozenset("0123456789abcdef")


class Entrypoint(BaseModel):
    """Declarative command and its default named arguments.

    Attributes:
        command: Executable and fixed positional arguments.
        args: Optional named arguments rendered by the launcher.

    Example:
        ``Entrypoint(command=["python", "train.py"], args={"seed": 42})``

    Raises:
        pydantic.ValidationError: If the command or argument mapping is invalid.
    """

    model_config = ConfigDict(extra="forbid")

    command: list[str]
    args: dict[str, ArgumentValue] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def validate_args_mapping(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("Entrypoint args must be a mapping")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValidationError("Entrypoint command must be non-empty strings")
        return value


class Metric(BaseModel):
    """Metric declaration used for validation and leaderboard direction.

    Attributes:
        name: Metric key emitted by a tracking backend.
        direction: Whether lower or higher values are preferred.

    Example:
        ``Metric(name="val/loss", direction="minimize")``
    """

    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


class Task(Record):
    """Top-level, file-backed research task.

    A task owns entrypoints, metric declarations, and tracking configuration.

    Attributes:
        id: Stable task identifier.
        markdown: Human-readable task notes.
        branch: Optional Git branch associated with the task.
        goal: Research goal or intended outcome.
        question: Research question investigated by the task.
        summary: Short task summary.
        entrypoints: Named launcher commands.
        metrics: Metrics displayed for task runs.
        tracking_backend: Task-level backend override.
        mlflow_experiment: Optional MLflow experiment name.
    """

    branch: str | None = None
    goal: str | None = Field(default=None, min_length=1)
    question: str | None = Field(default=None, min_length=1)
    summary: str | None = Field(default=None, min_length=1)
    # Runtime shape validation is intentionally performed by the model
    # validator below so malformed YAML gets a useful domain error.
    entrypoints: dict[str, Entrypoint] = Field(default_factory=dict)
    metrics: list[Metric] = Field(default_factory=list)
    tracking_backend: str | dict[str, Any] | None = None
    mlflow_experiment: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_entrypoint_names(self) -> Task:
        for name in self.entrypoints:
            validate_identifier(name, label="entrypoint name")
        return self

    @classmethod
    def create(
        cls,
        task_id: str,
        *,
        markdown: str = "",
        activate: bool = True,
        config: HirakuConfig | None = None,
        **fields: Any,
    ) -> Task:
        """Validate and persist a new task.

        Args:
            task_id: Identifier for the task.
            markdown: Optional Markdown body.
            activate: Whether to make the task active.
            config: Project configuration; discovered when omitted.
            **fields: Additional validated task fields.

        Returns:
            The persisted task.

        Raises:
            hiraku.errors.RecordExistsError: If the task already exists.
            pydantic.ValidationError: If supplied fields are invalid.

        Example:
            ``task = Task.create("demo", goal="Improve validation accuracy")``
        """
        selected = config or HirakuConfig.discover()
        record = cls(id=task_id, markdown=markdown, **fields)
        path = _task_path(task_id, selected)
        if path.exists():
            raise RecordExistsError(f"Task record already exists: {path}")
        record.save(path)
        if activate:
            selected.set_active_task(record)
        return record

    def experiments(self, *, config: HirakuConfig | None = None) -> list[Experiment]:
        """Return experiments belonging to this task.

        Args:
            config: Project configuration; discovered when omitted.

        Returns:
            Experiments sorted deterministically by identifier.
        """
        return list_experiments(task=self, config=config)

    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]:
        """Query runs belonging to this task.

        Args:
            backend: Tracking adapter; selected from configuration when omitted.
            config: Project configuration used for backend selection.
            **query: Backend options such as ``sort``, ``ascending``, or ``limit``.

        Returns:
            Normalized runs matching the task.

        Example:
            ``runs = task.runs(sort="val/loss", ascending=True)``
        """
        adapter = backend
        if adapter is None:
            selected = config or HirakuConfig.discover()
            adapter = select_backend(self, selected)
        return adapter.list_runs(task_id=self.id, experiment_id=None, **query)

    def entrypoint(self, name: str) -> Entrypoint:
        """Resolve a named task entrypoint.

        Args:
            name: Entrypoint identifier.

        Returns:
            The validated entrypoint declaration.

        Raises:
            hiraku.errors.EntrypointNotFoundError: If ``name`` is undefined.
        """
        try:
            return self.entrypoints[name]
        except KeyError as exc:
            raise EntrypointNotFoundError(
                f"Task {self.id!r} has no entrypoint {name!r}"
            ) from exc

    def delete(self, *, config: HirakuConfig | None = None) -> None:
        """Delete the task when it has no child experiments.

        Args:
            config: Project configuration; discovered when omitted.

        Raises:
            hiraku.errors.ValidationError: If experiments still belong to the task.
        """
        if self.experiments(config=config):
            raise ValidationError(
                f"Cannot delete task {self.id!r} while experiments exist"
            )
        super().delete()


class Experiment(Record):
    """Task-scoped experiment or proposal.

    An experiment is a concrete task attempt. A missing ``commit`` marks it as
    a proposal; committed experiments can be launched and compared.

    Attributes:
        id: Stable experiment identifier.
        task_id: Identifier of the owning task.
        summary: Short description of the experiment.
        hypothesis: Claim tested by the experiment.
        commit: Full source commit, or ``None`` for a proposal.
        branch: Optional Git branch.
        entrypoints: Experiment-specific command overrides.
        metrics: Optional metric declarations inherited from the task.
    """

    task_id: str
    summary: str | None = Field(default=None, min_length=1)
    hypothesis: str | None = Field(default=None, min_length=1)
    commit: str | None = None
    branch: str | None = None
    entrypoints: dict[str, Entrypoint] = Field(default_factory=dict)
    metrics: list[Metric] | None = None

    @property
    def is_proposal(self) -> bool:
        """Whether this experiment has not yet been assigned a commit."""
        return self.commit is None

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        validate_identifier(value, label="task ID")
        return value

    @field_validator("commit", mode="before")
    @classmethod
    def validate_commit_hash(cls, value: Any) -> str | None:
        if value is not None and (
            not isinstance(value, str)
            or len(value) not in {40, 64}
            or any(character not in _LOWER_HEXADECIMAL for character in value)
        ):
            raise ValidationError(
                "Commit must be a full lowercase "
                "Git hash (40 or 64 hexadecimal characters) or null"
            )
        return value

    @model_validator(mode="after")
    def validate_entrypoint_names(self) -> Experiment:
        for name in self.entrypoints:
            validate_identifier(name, label="entrypoint name")
        return self

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
    ) -> Experiment:
        """Validate and persist a new experiment.

        Args:
            experiment_id: Identifier for the experiment.
            task: Owning task object or identifier.
            commit: Optional full source commit.
            markdown: Optional Markdown body.
            activate: Whether to make the experiment active.
            config: Project configuration; discovered when omitted.
            **fields: Additional validated experiment fields.

        Returns:
            The persisted experiment.

        Raises:
            hiraku.errors.NoActiveTaskError: If no task can be resolved.
            hiraku.errors.RecordExistsError: If the experiment already exists.
            pydantic.ValidationError: If supplied fields are invalid.

        Example:
            ``experiment = Experiment.create("baseline", task=task, commit=sha)``
        """
        selected = config or HirakuConfig.discover()
        if task is None:
            if selected.active_task_id is None:
                raise NoActiveTaskError(
                    f"No active task for experiment {experiment_id!r}"
                )
            task_record = load_task(selected.active_task_id, config=selected)
        else:
            task_record = (
                task if isinstance(task, Task) else load_task(task, config=selected)
            )
        if "metrics" not in fields:
            fields["metrics"] = list(task_record.metrics)
        record = cls(
            id=experiment_id,
            task_id=task_record.id,
            commit=commit,
            markdown=markdown,
            **fields,
        )
        path = _experiment_path(task_record.id, experiment_id, selected)
        if path.exists():
            raise RecordExistsError(f"Experiment record already exists: {path}")
        record.save(path)
        if activate:
            selected.set_active_experiment(record)
        return record

    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]:
        """Query runs belonging to this experiment.

        Args:
            backend: Tracking adapter; selected from configuration when omitted.
            config: Project configuration used for backend selection.
            **query: Backend options such as ``sort``, ``ascending``, or ``limit``.

        Returns:
            Normalized runs matching this experiment.

        Example:
            ``runs = experiment.runs(limit=10)``
        """
        if backend is None:
            selected = config or HirakuConfig.discover()
            task = load_task(self.task_id, config=selected)
            backend = select_backend(task, selected)
        return backend.list_runs(task_id=self.task_id, experiment_id=self.id, **query)

    def entrypoint(self, name: str) -> Entrypoint:
        """Resolve a named experiment entrypoint.

        Args:
            name: Entrypoint identifier.

        Returns:
            The validated entrypoint declaration.

        Raises:
            hiraku.errors.EntrypointNotFoundError: If ``name`` is undefined.
        """
        try:
            return self.entrypoints[name]
        except KeyError as exc:
            raise EntrypointNotFoundError(
                f"Experiment {self.task_id}:{self.id} has no entrypoint {name!r}"
            ) from exc


def _task_path(task_id: str, config: HirakuConfig) -> Path:
    validate_identifier(task_id, label="task ID")
    return config.tasks_dir / task_id / "task.md"


def _experiment_path(task_id: str, experiment_id: str, config: HirakuConfig) -> Path:
    validate_identifier(task_id, label="task ID")
    validate_identifier(experiment_id, label="experiment ID")
    return config.tasks_dir / task_id / "experiments" / f"{experiment_id}.md"


def load_task(task_id: str, *, config: HirakuConfig | None = None) -> Task:
    """Resolve and load a task ID in a Hiraku project."""
    selected = config or HirakuConfig.discover()
    task = Task.load(_task_path(task_id, selected))
    if task.id != task_id:
        raise ValidationError(
            f"Task ID {task.id!r} does not match registry path for {task_id!r}"
        )
    return task


def list_tasks(*, config: HirakuConfig | None = None) -> list[Task]:
    """List tasks from the configured project registry in ID order."""
    selected = config or HirakuConfig.discover()
    tasks = Task.list(selected.tasks_dir.glob("*/task.md"))
    for task in tasks:
        if task.path != _task_path(task.id, selected):
            raise ValidationError(f"Task identity does not match its path: {task.path}")
    return tasks


def load_experiment(
    reference: str,
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> Experiment:
    """Resolve a scoped or qualified experiment reference in a project."""
    selected = config or HirakuConfig.discover()
    experiment_id = reference
    if ":" in reference:
        qualified_task, experiment_id = reference.split(":", 1)
        supplied_task = task.id if isinstance(task, Task) else task
        if supplied_task is not None and supplied_task != qualified_task:
            raise AmbiguousReferenceError(
                f"Conflicting task scopes for experiment {reference!r}"
            )
        task = qualified_task
    if task is None:
        task = selected.active_task_id
    if task is None:
        matches = sorted(selected.tasks_dir.glob(f"*/experiments/{experiment_id}.md"))
        if len(matches) > 1:
            raise AmbiguousReferenceError(
                f"Ambiguous experiment reference {experiment_id!r}"
            )
        raise NoActiveTaskError(f"No task scope for experiment {experiment_id!r}")
    task_record = task if isinstance(task, Task) else load_task(task, config=selected)
    experiment = Experiment.load(
        _experiment_path(task_record.id, experiment_id, selected)
    )
    if experiment.id != experiment_id or experiment.task_id != task_record.id:
        raise ValidationError(
            f"Experiment identity does not match its registry path: {experiment.path}"
        )
    return experiment


def list_experiments(
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> list[Experiment]:
    """List experiments for a task from the configured project registry."""
    selected = config or HirakuConfig.discover()
    if task is None:
        task = selected.active_task_id
    if task is None:
        raise NoActiveTaskError("No active task for listing experiments")
    task_record = task if isinstance(task, Task) else load_task(task, config=selected)
    experiments = Experiment.list(
        (selected.tasks_dir / task_record.id / "experiments").glob("*.md")
    )
    for experiment in experiments:
        expected = _experiment_path(task_record.id, experiment.id, selected)
        if experiment.task_id != task_record.id or experiment.path != expected:
            raise ValidationError(
                "Experiment identity does not match its registry path: "
                f"{experiment.path}"
            )
    return experiments


class Run(BaseModel):
    """Immutable normalized run returned by a tracking backend.

    Attributes:
        id: Backend run identifier.
        metrics: Observed metric values.
        params: Run parameters and configuration values.
        task_id: Owning task identifier, when available.
        experiment_id: Owning experiment identifier, when available.
        commit: Source commit evaluated by the run.
        timestamp: Run creation timestamp.
        entrypoint: Logical entrypoint name.
        url: Optional backend URL.
        raw: Original backend payload.

    Example:
        ``Run(id="r-001", metrics={"accuracy": 0.91})``

    Raises:
        pydantic.ValidationError: If the run identifier or field values are invalid.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    params: Mapping[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    experiment_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    entrypoint: str | None = None
    url: str | None = None
    raw: Mapping[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not value:
            raise ValidationError("Run ID must be non-empty")
        return value

    @model_validator(mode="after")
    def normalize(self) -> Run:
        from types import MappingProxyType

        for name in ("metrics", "params", "raw"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValidationError(f"Run {name} must be a mapping")
            object.__setattr__(self, name, MappingProxyType(dict(value)))
        if self.timestamp is not None:
            value = self.timestamp
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            object.__setattr__(self, "timestamp", value.astimezone(UTC))
        return self
