"""Named, file-based leaderboards over normalized tracking runs."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import BackendError, ValidationError
from ..io import dump_yaml, load_yaml_mapping
from ..project import Project
from ..record import validate_identifier
from .base import (
    BackendConfig,
    CSVBackendConfig,
    MLflowBackendConfig,
    PythonBackendConfig,
    Run,
)
from .csv import CSVBackend
from .mlflow import MLflowBackend
from .python import PythonBackend, load_definition
from .wandb import WandbBackend


class Metric(BaseModel):
    """A displayed metric with an optional optimization direction."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"\S")
    direction: Literal["minimize", "maximize"] | None = None


class Leaderboard(BaseModel):
    """One tracking source and optional ordered metric declarations.

    Parameters are always discovered from runs. Metric declarations affect
    presentation, never which values an adapter reads or returns.
    """

    model_config = ConfigDict(extra="forbid")
    source: BackendConfig
    # The list() method shadows the builtin during model annotation evaluation.
    metrics: builtins.list[Metric] | None = None

    @classmethod
    def create(cls, project: Project, name: str, source: dict[str, Any]) -> Leaderboard:
        """Validate and create a definition, refusing to replace existing files."""
        path = _leaderboard_path(project, name)
        if path.exists():
            raise ValidationError(f"Leaderboard already exists: {name}")
        try:
            leaderboard = cls.model_validate({"source": source})
        except (ValueError, BackendError) as exc:
            raise ValidationError(f"Invalid leaderboard {name!r}: {exc}") from exc
        content = dump_yaml(leaderboard.model_dump(mode="json", exclude_none=True))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError as exc:
            raise ValidationError(f"Leaderboard already exists: {name}") from exc
        except OSError as exc:
            raise ValidationError(f"Cannot create leaderboard {name!r}: {exc}") from exc
        return leaderboard

    @classmethod
    def load(cls, project: Project, name: str | None = None) -> Leaderboard:
        """Load a named leaderboard, or automatically select the sole definition."""
        if name is None:
            names = cls.list(project)
            if not names:
                raise ValidationError(
                    f"No leaderboards. Create {project.leaderboards_dir / '<name>.yml'}"
                )
            if len(names) != 1:
                raise ValidationError(
                    "Select a leaderboard with --leaderboard; "
                    f"available: {', '.join(names)}"
                )
            name = names[0]
        path = _leaderboard_path(project, name)
        try:
            if path.suffix == ".py":
                module = load_definition(path)
                return cls.model_validate(
                    {
                        "source": {"type": "python", "path": path.resolve()},
                        "metrics": getattr(module, "metrics", None),
                    }
                )
            return cls.model_validate(load_yaml_mapping(path))
        except (ValueError, BackendError) as exc:
            raise ValidationError(f"Invalid leaderboard {path}: {exc}") from exc

    @staticmethod
    def list(project: Project) -> builtins.list[str]:
        """List YAML and Python names without executing definitions."""
        names: set[str] = set()
        directory = project.leaderboards_dir
        for path in directory.iterdir() if directory.exists() else ():
            if not path.is_file() or path.suffix not in (".yml", ".py"):
                continue
            if path.stem in names:
                raise ValidationError(f"Duplicate leaderboard name: {path.stem}")
            names.add(path.stem)
        return sorted(names)

    @staticmethod
    def delete(project: Project, name: str) -> None:
        """Delete a definition, including malformed YAML, leaving source data intact."""
        path = _leaderboard_path(project, name)
        try:
            path.unlink()
        except OSError as exc:
            raise ValidationError(f"Cannot delete leaderboard {name!r}: {exc}") from exc

    @field_validator("metrics")
    @classmethod
    def unique_metrics(
        cls, value: builtins.list[Metric] | None
    ) -> builtins.list[Metric] | None:
        if value is not None and len({item.name for item in value}) != len(value):
            raise ValueError("Metric names must be unique")
        return value

    def metric_names(self, runs: builtins.list[Run]) -> builtins.list[str]:
        """Return declared names, or discover metrics in source order."""
        if self.metrics is not None:
            return [item.name for item in self.metrics]
        return list(dict.fromkeys(name for run in runs for name in run.metrics))

    def list_runs(
        self,
        project: Project,
        *,
        record_id: str | None = None,
        include_unattributed: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> builtins.list[Run]:
        """Query attributed runs, optionally filtering by record and sorting by metric.

        Set ``include_unattributed`` to include runs without a record ID.
        A record file need not exist for a run to count as attributed.

        Metric names are plain names, even when they collide with run metadata
        or parameters. Direction defaults to the metric declaration, then ascending.
        """
        if ascending is None and sort is not None:
            ascending = not any(
                metric.name == sort and metric.direction == "maximize"
                for metric in self.metrics or ()
            )
        return self._create_backend(project).list_runs(
            record_id=record_id,
            include_unattributed=include_unattributed,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )

    def _create_backend(
        self, project: Project
    ) -> CSVBackend | MLflowBackend | WandbBackend | PythonBackend:
        """Construct an adapter directly from the validated source settings."""
        if isinstance(self.source, CSVBackendConfig):
            return CSVBackend(project, settings=self.source)
        if isinstance(self.source, MLflowBackendConfig):
            return MLflowBackend(project, settings=self.source)
        if isinstance(self.source, PythonBackendConfig):
            return PythonBackend(project, settings=self.source)
        return WandbBackend(self.source.project, settings=self.source)


def _leaderboard_path(project: Project, name: str) -> Path:
    """Return the file path for a validated, flat leaderboard name."""
    validate_identifier(name, label="leaderboard name")
    paths = [project.leaderboards_dir / f"{name}{suffix}" for suffix in (".yml", ".py")]
    existing = [path for path in paths if path.is_file()]
    if len(existing) > 1:
        raise ValidationError(f"Duplicate leaderboard name: {name}")
    return existing[0] if existing else paths[0]
