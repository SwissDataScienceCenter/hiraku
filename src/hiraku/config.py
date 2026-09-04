"""Project discovery, initialization, and configuration."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .errors import ProjectNotFoundError, ValidationError
from .io import atomic_write, dump_yaml, load_yaml_mapping


class HirakuConfig(BaseModel):
    """Discovered Hiraku project and validated configuration.

    Attributes:
        path: Path to the project configuration file.
        root: Project root directory.
        hiraku_dir_name: Relative state directory name.
        default_dispatch: Default launcher script.
        task_branch: Task branch template.
        experiment_branch: Experiment branch template.
        default_program: Default program identifier.
        tracking_backend: Backend configuration mapping with a required ``type``.

    Raises:
        pydantic.ValidationError: If configuration values are invalid or inconsistent.

    Note:
        Runtime paths are resolved from ``root`` and excluded from YAML serialization.

    """

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "hiraku_dir": "hiraku",
        "default_dispatch": "launch.sh",
        "task_branch": "hiraku/tasks/{task_id}",
        "experiment_branch": "hiraku/experiments/{task_id}/{experiment_id}",
        "default_program": "interactive",
        "tracking_backend": {"type": "csv"},
        "active_task_id": None,
        "active_experiment_id": None,
    }
    model_config = ConfigDict(
        extra="allow", validate_assignment=True, populate_by_name=True
    )

    path: Path = Field(exclude=True)
    root: Path = Field(exclude=True)
    hiraku_dir_name: str = Field(
        default=DEFAULTS["hiraku_dir"], alias="hiraku_dir", min_length=1
    )
    default_dispatch: str = Field(default=DEFAULTS["default_dispatch"], min_length=1)
    task_branch: str = Field(default=DEFAULTS["task_branch"], min_length=1)
    experiment_branch: str = Field(default=DEFAULTS["experiment_branch"], min_length=1)
    default_program: str = Field(default=DEFAULTS["default_program"], min_length=1)
    tracking_backend: dict[str, Any] = Field(
        default_factory=lambda: dict(HirakuConfig.DEFAULTS["tracking_backend"])
    )
    active_task_id: str | None = DEFAULTS["active_task_id"]
    active_experiment_id: str | None = DEFAULTS["active_experiment_id"]
    _hiraku_dir: Path = PrivateAttr()

    @model_validator(mode="before")
    @classmethod
    def normalize_tracking_backend(cls, values: Any) -> Any:
        """Normalize shorthand backend names to the common mapping syntax."""
        if not isinstance(values, Mapping):
            return values
        raw = dict(values)
        backend = raw.get("tracking_backend")
        if isinstance(backend, str):
            raw["tracking_backend"] = {"type": backend}
        elif backend is not None and not isinstance(backend, Mapping):
            raise ValidationError(
                "tracking_backend must be a backend name or mapping with a type"
            )
        elif isinstance(backend, Mapping):
            backend_mapping = dict(backend)
            if not isinstance(backend_mapping.get("type"), str) or not backend_mapping[
                "type"
            ]:
                raise ValidationError(
                    "tracking_backend mapping requires a non-empty string type"
                )
            raw["tracking_backend"] = backend_mapping
        return raw

    @model_validator(mode="after")
    def validate_config(self) -> HirakuConfig:
        """Validate backend consistency and resolve the state directory."""
        if not self.hiraku_dir_name:
            raise ValidationError("Configuration key 'hiraku_dir' must be a string")
        raw = Path(self.hiraku_dir_name)
        if raw.is_absolute():
            resolved = raw.resolve(strict=False)
        else:
            if ".." in raw.parts:
                raise ValidationError("hiraku_dir cannot contain parent traversal")
            resolved = (self.root / raw).resolve(strict=False)
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValidationError(
                f"hiraku_dir resolves outside project root {self.root}: {resolved}"
            ) from exc
        object.__setattr__(self, "_hiraku_dir", resolved)
        return self

    @classmethod
    def load(cls, path: Path | str) -> HirakuConfig:
        """Load and validate a project configuration from YAML.

        Args:
            path: Configuration file path.

        Returns:
            A validated project configuration.

        Raises:
            hiraku.errors.ValidationError: If the file is unreadable or invalid.
        """
        marker = Path(path).expanduser().absolute()
        return cls(path=marker, root=marker.parent, **load_yaml_mapping(marker))

    @classmethod
    def discover(cls, start: Path | str | None = None) -> HirakuConfig:
        """Discover the nearest project configuration.

        Args:
            start: Directory or file from which to search upward. Defaults to the
                current working directory.

        Returns:
            The nearest validated configuration.

        Raises:
            hiraku.errors.ProjectNotFoundError: If no configuration is found.
        """
        candidate = Path.cwd() if start is None else Path(start)
        candidate = candidate.expanduser().absolute()
        if candidate.is_file():
            candidate = candidate.parent
        for directory in (candidate, *candidate.parents):
            marker = directory / "hiraku.yml"
            if marker.is_file():
                return cls.load(marker)
        raise ProjectNotFoundError(f"No hiraku.yml found from {candidate}")

    @classmethod
    def init(
        cls, root: Path | str = ".", *, install_skill: bool = True
    ) -> HirakuConfig:
        """Initialize a new Hiraku project.

        Args:
            root: Directory to initialize.
            install_skill: Whether to install the repository-local agent skill.

        Returns:
            The initialized configuration.

        Raises:
            hiraku.errors.ProjectExistsError: If the project is already initialized.
        """
        from .project import ProjectInitializer

        return ProjectInitializer.create(root, install_skill=install_skill)

    def save(self) -> None:
        """Persist validated configuration to its YAML file.

        Raises:
            hiraku.errors.ValidationError: If the configuration cannot be written.

        Example:
            ```python
            config = HirakuConfig.discover()
            config.save()
            ```
        """
        try:
            atomic_write(self.path, dump_yaml(self.to_dict()))
        except OSError as exc:
            raise ValidationError(
                f"Cannot save configuration at {self.path}: {exc}"
            ) from exc

    @property
    def hiraku_dir(self) -> Path:
        """Return the resolved Hiraku state directory."""
        return self._hiraku_dir

    def to_dict(self) -> dict[str, Any]:
        """Return user-managed fields suitable for YAML persistence.

        Returns:
            A serializable mapping excluding runtime paths and private state.

        Example:
            ``yaml.safe_dump(config.to_dict())``
        """
        return self.model_dump(
            by_alias=True,
            exclude={"path", "root", "_hiraku_dir"},
            exclude_none=False,
        )

    @property
    def tasks_dir(self) -> Path:
        """Return the directory containing task records."""
        return self.hiraku_dir / "tasks"

    @property
    def programs_dir(self) -> Path:
        """Return the directory containing agent program records."""
        return self.hiraku_dir / "programs"

    @property
    def actions_dir(self) -> Path:
        """Return the directory containing agent action records."""
        return self.hiraku_dir / "actions"

    @property
    def contexts_dir(self) -> Path:
        """Return the directory containing curated context records."""
        return self.hiraku_dir / "contexts"

    @property
    def dispatch_dir(self) -> Path:
        """Return the directory containing launcher dispatch scripts."""
        return self.hiraku_dir / "dispatch"

    def set_active_task(self, task: str | Any) -> None:
        """Set the active task and clear incompatible experiment state.

        Args:
            task: Task identifier or loaded ``Task`` instance.

        Raises:
            hiraku.errors.RecordNotFoundError: If the task cannot be loaded.
            hiraku.errors.ValidationError: If configuration persistence fails.

        Example:
            ``config.set_active_task("heart-disease")``
        """
        from .data import Task, load_task

        identifier = task.id if isinstance(task, Task) else task
        record = load_task(identifier, config=self)
        old = (self.active_task_id, self.active_experiment_id)
        try:
            self.active_task_id = record.id
            if old[0] != record.id:
                self.active_experiment_id = None
            self.save()
        except Exception:
            self.active_task_id, self.active_experiment_id = old
            raise

    def set_active_experiment(self, experiment: str | Any) -> None:
        """Set the active experiment and its owning task.

        Args:
            experiment: Qualified experiment reference or loaded ``Experiment``.

        Raises:
            hiraku.errors.RecordNotFoundError: If the experiment cannot be loaded.
            hiraku.errors.ValidationError: If configuration persistence fails.

        Example:
            ``config.set_active_experiment("heart-disease:random-forest")``
        """
        from .data import Experiment, load_experiment

        reference = (
            f"{experiment.task_id}:{experiment.id}"
            if isinstance(experiment, Experiment)
            else experiment
        )
        record = load_experiment(reference, config=self)
        old = (self.active_task_id, self.active_experiment_id)
        try:
            self.active_task_id = record.task_id
            self.active_experiment_id = record.id
            self.save()
        except Exception:
            self.active_task_id, self.active_experiment_id = old
            raise

    def clear_active_experiment(self) -> None:
        """Clear the active experiment while retaining the active task.

        Raises:
            hiraku.errors.ValidationError: If configuration persistence fails.
        """
        old = self.active_experiment_id
        try:
            self.active_experiment_id = None
            self.save()
        except Exception:
            self.active_experiment_id = old
            raise

    def clear_active_task(self) -> None:
        """Atomically clear the active task and its experiment selection."""
        old = (self.active_task_id, self.active_experiment_id)
        try:
            self.active_task_id = None
            self.active_experiment_id = None
            self.save()
        except Exception:
            self.active_task_id, self.active_experiment_id = old
            raise
