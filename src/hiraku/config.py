"""Project discovery, initialization, and configuration."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from contextlib import suppress
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, ClassVar

from .errors import (
    HirakuError,
    ProjectExistsError,
    ProjectNotFoundError,
    ValidationError,
)
from .io import atomic_write, dump_yaml, load_yaml_mapping


class HirakuConfig:
    """A discovered Hiraku project and its validated configuration."""

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "hiraku_dir": "hiraku",
        "default_dispatch": "launch.sh",
        "task_branch": "hiraku/tasks/{task_id}",
        "experiment_branch": "hiraku/experiment/{task_id}/{experiment_id}",
        "default_program": "interactive",
        "tracking_backend": "csv",
        "active_task_id": None,
        "active_experiment_id": None,
    }
    _STRING_KEYS: ClassVar[set[str]] = {
        "hiraku_dir",
        "default_dispatch",
        "task_branch",
        "experiment_branch",
        "default_program",
        "tracking_backend",
    }

    def __init__(self, path: Path | str, data: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path).absolute()
        self.root = self.path.parent
        loaded = load_yaml_mapping(self.path) if data is None else dict(data)
        self._data = {**self.DEFAULTS, **loaded}
        self._validate()

    @classmethod
    def discover(cls, start: Path | str | None = None) -> HirakuConfig:
        candidate = Path.cwd() if start is None else Path(start)
        candidate = candidate.expanduser().absolute()
        if candidate.is_file():
            candidate = candidate.parent
        for directory in (candidate, *candidate.parents):
            marker = directory / "hiraku.yml"
            if marker.is_file():
                return cls(marker)
        raise ProjectNotFoundError(f"No hiraku.yml found from {candidate}")

    @classmethod
    def init(
        cls, root: Path | str = ".", *, install_skill: bool = True
    ) -> HirakuConfig:
        project_root = Path(root).expanduser().absolute()
        marker = project_root / "hiraku.yml"
        if marker.exists():
            raise ProjectExistsError(f"Hiraku project already exists at {marker}")
        created_files: list[Path] = []
        created_dirs: list[Path] = []
        root_existed = project_root.exists()
        data = dict(cls.DEFAULTS)
        try:
            project_root.mkdir(parents=True, exist_ok=True)
            if not root_existed:
                created_dirs.append(project_root)
            atomic_write(marker, dump_yaml(data))
            created_files.append(marker)
            config = cls(marker, data)
            for directory in (
                config.tasks_dir,
                config.programs_dir,
                config.actions_dir,
                config.contexts_dir,
                config.dispatch_dir,
            ):
                if not directory.exists():
                    directory.mkdir(parents=True)
                    created_dirs.append(directory)
            resource_root = files("hiraku.resources")
            copies = [
                (resource_root / "programs", config.programs_dir),
                (resource_root / "actions", config.actions_dir),
                (resource_root / "dispatch", config.dispatch_dir),
            ]
            if install_skill:
                copies.append(
                    (resource_root / "skill", project_root / ".agents/skills/hiraku")
                )
            for source, target in copies:
                with as_file(source) as source_path:
                    cls._copy_resource_tree(
                        source_path, target, created_files, created_dirs
                    )
            launcher = config.dispatch_dir / "launch.sh"
            launcher.chmod(launcher.stat().st_mode | 0o111)
            return config
        except Exception as exc:
            for path in reversed(created_files):
                path.unlink(missing_ok=True)
            for path in sorted(
                created_dirs, key=lambda item: len(item.parts), reverse=True
            ):
                with suppress(OSError):
                    path.rmdir()
            if isinstance(exc, HirakuError):
                raise
            raise ValidationError(
                f"Could not initialize Hiraku project at {project_root}: {exc}"
            ) from exc

    @staticmethod
    def _copy_resource_tree(
        source: Path, target: Path, created_files: list[Path], created_dirs: list[Path]
    ) -> None:
        missing: list[Path] = []
        current = target
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
            created_dirs.append(directory)
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            destination = target / relative
            if item.is_dir():
                if not destination.exists():
                    destination.mkdir(parents=True)
                    created_dirs.append(destination)
            elif destination.exists():
                raise ValidationError(
                    "Initialization resource would overwrite existing file: "
                    f"{destination}"
                )
            else:
                shutil.copyfile(item, destination)
                created_files.append(destination)

    def _validate(self) -> None:
        for key in self._STRING_KEYS:
            if not isinstance(self._data[key], str) or not self._data[key]:
                raise ValidationError(f"Configuration key {key!r} must be a string")
        for key in ("active_task_id", "active_experiment_id"):
            if self._data[key] is not None and not isinstance(self._data[key], str):
                raise ValidationError(
                    f"Configuration key {key!r} must be a string or null"
                )
        raw = Path(self._data["hiraku_dir"])
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
        self._hiraku_dir = resolved

    def save(self) -> None:
        self._validate()
        try:
            atomic_write(self.path, dump_yaml(self._data))
        except OSError as exc:
            raise ValidationError(
                f"Cannot save configuration at {self.path}: {exc}"
            ) from exc

    @property
    def hiraku_dir(self) -> Path:
        return self._hiraku_dir

    @property
    def tasks_dir(self) -> Path:
        return self.hiraku_dir / "tasks"

    @property
    def programs_dir(self) -> Path:
        return self.hiraku_dir / "programs"

    @property
    def actions_dir(self) -> Path:
        return self.hiraku_dir / "actions"

    @property
    def contexts_dir(self) -> Path:
        return self.hiraku_dir / "contexts"

    @property
    def dispatch_dir(self) -> Path:
        return self.hiraku_dir / "dispatch"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def active_task_id(self) -> str | None:
        return self._data["active_task_id"]

    @property
    def active_experiment_id(self) -> str | None:
        return self._data["active_experiment_id"]

    def set_active_task(self, task: str | Any) -> None:
        from .data import Task

        identifier = task.id if isinstance(task, Task) else task
        record = Task.load(identifier, config=self)
        new_data = dict(self._data)
        new_data["active_task_id"] = record.id
        if self.active_task_id != record.id:
            new_data["active_experiment_id"] = None
        self._replace_data(new_data)

    def set_active_experiment(self, experiment: str | Any) -> None:
        from .data import Experiment

        reference = (
            f"{experiment.task_id}:{experiment.id}"
            if isinstance(experiment, Experiment)
            else experiment
        )
        record = Experiment.load(reference, config=self)
        new_data = dict(self._data)
        new_data["active_task_id"] = record.task_id
        new_data["active_experiment_id"] = record.id
        self._replace_data(new_data)

    def clear_active_experiment(self) -> None:
        new_data = dict(self._data)
        new_data["active_experiment_id"] = None
        self._replace_data(new_data)

    def _replace_data(self, data: dict[str, Any]) -> None:
        original = self._data
        self._data = data
        try:
            self.save()
        except Exception:
            self._data = original
            raise
