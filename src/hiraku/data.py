"""Tasks, experiments, entrypoints, metrics, and normalized runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, TypeAlias, TypedDict

from .config import HirakuConfig
from .errors import (
    AmbiguousReferenceError,
    EntrypointNotFoundError,
    NoActiveTaskError,
    RecordExistsError,
    ValidationError,
)
from .records import Record, validate_identifier
from .tracking import TrackingBackend, select_backend

ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]
_LOWER_HEXADECIMAL = frozenset("0123456789abcdef")


class Entrypoint(TypedDict):
    """A declarative command and its default named arguments."""

    command: Sequence[str]
    args: NotRequired[Mapping[str, ArgumentValue]]


class Metric(TypedDict):
    """A metric presentation declaration."""

    name: str
    direction: Literal["minimize", "maximize"]


def _validate_metrics(metrics: Any, owner: str) -> None:
    if not isinstance(metrics, Sequence) or isinstance(metrics, str | bytes):
        raise ValidationError(f"Metrics for {owner} must be a sequence")
    for metric in metrics:
        if not isinstance(metric, Mapping) or set(metric) != {"name", "direction"}:
            raise ValidationError(f"Invalid metric declaration for {owner}: {metric!r}")
        if not isinstance(metric["name"], str) or not metric["name"]:
            raise ValidationError(f"Metric name for {owner} must be non-empty")
        if metric["direction"] not in {"minimize", "maximize"}:
            raise ValidationError(
                f"Invalid metric direction for {owner}: {metric['direction']!r}"
            )


def _validate_entrypoints(entrypoints: Any, owner: str) -> None:
    if not isinstance(entrypoints, Mapping):
        raise ValidationError(f"Entrypoints for {owner} must be a mapping")
    for name, entrypoint in entrypoints.items():
        validate_identifier(name, label="entrypoint name")
        if not isinstance(entrypoint, Mapping):
            raise ValidationError(f"Entrypoint {name!r} for {owner} must be a mapping")
        from .launch import validate_entrypoint

        validate_entrypoint(entrypoint)


class Task(Record):
    """A top-level, file-backed research task."""

    _core_fields = {"id", "branch", "entrypoints", "metrics", "tracking_backend"}

    def __init__(self, task_id: str, *, markdown: str = "", **fields: Any) -> None:
        super().__init__(task_id, markdown=markdown, **fields)

    @property
    def branch(self) -> str | None:
        return self._data.get("branch")

    @branch.setter
    def branch(self, value: str | None) -> None:
        self._data["branch"] = value

    @property
    def entrypoints(self) -> Mapping[str, Entrypoint]:
        return self._data.get("entrypoints", {})

    @entrypoints.setter
    def entrypoints(self, value: Mapping[str, Entrypoint]) -> None:
        self._data["entrypoints"] = value

    @property
    def metrics(self) -> Sequence[Metric]:
        return self._data.get("metrics", ())

    @metrics.setter
    def metrics(self, value: Sequence[Metric]) -> None:
        self._data["metrics"] = value

    @property
    def tracking_backend(self) -> str | Mapping[str, Any] | None:
        return self._data.get("tracking_backend")

    @tracking_backend.setter
    def tracking_backend(self, value: str | Mapping[str, Any] | None) -> None:
        self._data["tracking_backend"] = value

    def validate(self) -> None:
        super().validate()
        if self.branch is not None and not isinstance(self.branch, str):
            raise ValidationError(
                f"Task branch for {self.id!r} must be a string or null"
            )
        if "entrypoints" in self:
            _validate_entrypoints(self.entrypoints, self.id)
        if "metrics" in self:
            _validate_metrics(self.metrics, self.id)
        if self.tracking_backend is not None and not isinstance(
            self.tracking_backend, str | Mapping
        ):
            raise ValidationError(f"Invalid tracking backend for task {self.id!r}")

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
        selected = config or HirakuConfig.discover()
        record = cls(task_id, markdown=markdown, **fields)
        path = _task_path(task_id, selected)
        if path.exists():
            raise RecordExistsError(f"Task record already exists: {path}")
        record.save(path)
        if activate:
            selected.set_active_task(record)
        return record

    def experiments(self, *, config: HirakuConfig | None = None) -> list[Experiment]:
        return list_experiments(task=self, config=config)

    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]:
        """Query runs filtered to this task."""
        adapter = backend
        if adapter is None:
            selected = config or HirakuConfig.discover()
            adapter = select_backend(self, selected)
        return adapter.list_runs(task_id=self.id, experiment_id=None, **query)

    def entrypoint(self, name: str) -> Entrypoint:
        try:
            return self.entrypoints[name]
        except KeyError as exc:
            raise EntrypointNotFoundError(
                f"Task {self.id!r} has no entrypoint {name!r}"
            ) from exc

    def delete(self, *, config: HirakuConfig | None = None) -> None:
        if self.experiments(config=config):
            raise ValidationError(
                f"Cannot delete task {self.id!r} while experiments exist"
            )
        super().delete()


class Experiment(Record):
    """A task-scoped experiment, or proposal when it has no commit."""

    _core_fields = {"id", "task_id", "commit", "branch", "entrypoints", "metrics"}

    def __init__(
        self,
        experiment_id: str,
        *,
        task: Task | str | None = None,
        commit: str | None = None,
        markdown: str = "",
        **fields: Any,
    ) -> None:
        task_id = task.id if isinstance(task, Task) else task
        if task_id is not None:
            fields = {"task_id": task_id, **fields}
        if commit is not None:
            fields = {**fields, "commit": commit}
        super().__init__(experiment_id, markdown=markdown, **fields)
        if isinstance(task, Task):
            object.__setattr__(self, "_task", task)

    @property
    def task_id(self) -> str:
        return self._data.get("task_id")

    @task_id.setter
    def task_id(self, value: str) -> None:
        self._data["task_id"] = value

    @property
    def commit(self) -> str | None:
        return self._data.get("commit")

    @commit.setter
    def commit(self, value: str | None) -> None:
        self._data["commit"] = value

    @property
    def branch(self) -> str | None:
        return self._data.get("branch")

    @branch.setter
    def branch(self, value: str | None) -> None:
        self._data["branch"] = value

    @property
    def entrypoints(self) -> Mapping[str, Entrypoint]:
        return self._data.get("entrypoints", {})

    @entrypoints.setter
    def entrypoints(self, value: Mapping[str, Entrypoint]) -> None:
        self._data["entrypoints"] = value

    @property
    def metrics(self) -> Sequence[Metric]:
        if "metrics" in self._data:
            return self._data["metrics"]
        task = getattr(self, "_task", None)
        return task.metrics if isinstance(task, Task) else ()

    @metrics.setter
    def metrics(self, value: Sequence[Metric]) -> None:
        self._data["metrics"] = value

    @property
    def is_proposal(self) -> bool:
        return self.commit is None

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
        record = cls(
            experiment_id,
            task=task_record,
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

    def validate(self) -> None:
        super().validate()
        validate_identifier(self.task_id, label="task ID")
        if self.commit is not None and (
            not isinstance(self.commit, str)
            or len(self.commit) not in {40, 64}
            or any(character not in _LOWER_HEXADECIMAL for character in self.commit)
        ):
            raise ValidationError(
                f"Commit for experiment {self.id!r} must be a full lowercase "
                "Git hash (40 or 64 hexadecimal characters) or null"
            )
        if self.branch is not None and not isinstance(self.branch, str):
            raise ValidationError(
                f"Branch for experiment {self.id!r} must be a string or null"
            )
        if "entrypoints" in self:
            _validate_entrypoints(self.entrypoints, self.id)
        if "metrics" in self:
            _validate_metrics(self._data["metrics"], self.id)

    def runs(
        self,
        *,
        backend: TrackingBackend | None = None,
        config: HirakuConfig | None = None,
        **query: Any,
    ) -> list[Run]:
        """Query runs filtered to this task and experiment."""
        if backend is None:
            selected = config or HirakuConfig.discover()
            task = load_task(self.task_id, config=selected)
            backend = select_backend(task, selected)
        return backend.list_runs(task_id=self.task_id, experiment_id=self.id, **query)

    def entrypoint(self, name: str) -> Entrypoint:
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
    object.__setattr__(experiment, "_task", task_record)
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
        object.__setattr__(experiment, "_task", task_record)
    return experiments


@dataclass(frozen=True)
class Run:
    """An immutable normalized tracking-backend run."""

    id: str
    metrics: Mapping[str, Any]
    params: Mapping[str, Any]
    task_id: str | None = None
    experiment_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    entrypoint: str | None = None
    url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        from types import MappingProxyType

        if not self.id:
            raise ValidationError("Run ID must be non-empty")
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
