"""Read-only tracking backend selection and CSV/MLflow adapters."""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import HirakuConfig
from .errors import BackendError, ValidationError

if TYPE_CHECKING:
    from .data import Run, Task


class CSVBackendConfig(BaseModel):
    """Validated options for the CSV tracking backend.

    Attributes:
        type: Backend discriminator; must be ``"csv"``.
        path: Optional CSV path override.

    Example:
        ``CSVBackendConfig(path="results/runs.csv")``
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["csv"] = "csv"
    path: Path | None = None


class MLflowBackendConfig(BaseModel):
    """Validated options for the MLflow tracking backend.

    Attributes:
        type: Backend discriminator; must be ``"mlflow"``.
        tracking_uri: Optional remote or configured MLflow URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.

    Raises:
        pydantic.ValidationError: If both URI and database are configured.

    Example:
        ``MLflowBackendConfig(database="mlflow.db")``
    Warning:
        Configure either ``tracking_uri`` or ``database``, never both.

    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["mlflow"] = "mlflow"
    tracking_uri: str | None = Field(default=None, min_length=1)
    experiment_name: str | None = Field(default=None, min_length=1)
    database: Path | None = None

    @model_validator(mode="after")
    def validate_sources(self) -> MLflowBackendConfig:
        if self.tracking_uri and self.database:
            raise BackendError(
                "mlflow backend cannot set both tracking_uri and database"
            )
        return self


BackendConfig = CSVBackendConfig | MLflowBackendConfig


def _parse_backend_config(specification: str | Mapping[str, Any]) -> BackendConfig:
    if isinstance(specification, str):
        raw: dict[str, Any] = {"type": specification}
    elif isinstance(specification, Mapping):
        raw = dict(specification)
        if "type" not in raw and "name" in raw:
            raw["type"] = raw.pop("name")
    else:
        raise BackendError("Tracking backend must be a name or mapping")
    name = raw.get("type")
    try:
        if name == "csv":
            return CSVBackendConfig.model_validate(raw)
        if name == "mlflow":
            return MLflowBackendConfig.model_validate(raw)
    except Exception as exc:
        raise BackendError(
            f"Invalid {name!r} tracking backend configuration: {exc}"
        ) from exc
    raise BackendError(f"Unknown tracking backend: {name!r}")


class TrackingBackend(ABC):
    """Abstract contract implemented by every run-tracking adapter."""

    @staticmethod
    def _validate_filters(task_id: str, experiment_id: str | None) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise BackendError("Tracking queries require a non-empty task_id")
        if experiment_id is not None and (
            not isinstance(experiment_id, str) or not experiment_id
        ):
            raise BackendError("experiment_id must be a non-empty string or null")

    @staticmethod
    def _timestamp(value: Any, *, backend: str, context: str) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, int | float):
                parsed = datetime.fromtimestamp(value / 1000, tz=UTC)
            elif isinstance(value, str):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                raise TypeError(type(value).__name__)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (ValueError, TypeError, OSError) as exc:
            raise BackendError(
                f"{backend} backend returned invalid timestamp for {context}: {value!r}"
            ) from exc

    @staticmethod
    def _apply_query(
        runs: list[Run], query: Mapping[str, Any], backend: str
    ) -> list[Run]:
        allowed = {"sort", "ascending", "limit"}
        unknown = set(query) - allowed
        if unknown:
            raise BackendError(
                f"{backend} backend does not support query options: {sorted(unknown)}"
            )
        result = list(runs)
        sort = query.get("sort")
        if sort is None and "ascending" in query:
            raise BackendError(f"{backend} backend ascending requires a sort field")
        if sort is not None:
            if not isinstance(sort, str):
                raise BackendError(f"{backend} backend sort must be a string")

            def value(run: Run) -> Any:
                value = getattr(run, sort, None)
                if value is None:
                    value = run.metrics.get(sort, run.params.get(sort))
                return value

            def numeric(run: Run) -> float | None:
                candidate = value(run)
                if isinstance(candidate, bool) or candidate is None:
                    return None
                try:
                    return float(candidate)
                except (TypeError, ValueError):
                    return None

            ranked = [run for run in result if numeric(run) is not None]
            unranked = [run for run in result if numeric(run) is None]
            ranked.sort(key=lambda run: run.id)
            ranked.sort(
                key=numeric,
                reverse=not bool(query.get("ascending", True)),
            )
            result = [*ranked, *sorted(unranked, key=lambda run: run.id)]
        else:
            result.sort(
                key=lambda run: (
                    run.timestamp is None,
                    run.timestamp or datetime.max.replace(tzinfo=UTC),
                    run.id,
                )
            )
        limit = query.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise BackendError(
                    f"{backend} backend limit must be a non-negative integer"
                )
            result = result[:limit]
        return result

    def list_runs(
        self, *, task_id: str, experiment_id: str | None = None, **query: Any
    ) -> list[Run]:
        """Return normalized runs for a task and optional experiment.

        Args:
            task_id: The task ID to filter runs by.
            experiment_id: An optional experiment ID to filter runs by.
            query: Optional query parameters to sort, limit, or filter the results.

        Returns:
            A list of normalized Run objects matching the specified filters and query.
        """

        self._validate_filters(task_id, experiment_id)
        runs = self._read_runs(task_id=task_id, experiment_id=experiment_id)
        runs = [
            run
            for run in runs
            if run.task_id in {None, task_id}
            and (experiment_id is None or run.experiment_id == experiment_id)
        ]
        return self._apply_query(runs, query, self.backend_name)

    @property
    def backend_name(self) -> str:
        return type(self).__name__.removesuffix("Backend").lower()

    @abstractmethod
    def _read_runs(
        self, *, task_id: str, experiment_id: str | None = None
    ) -> list[Run]:
        raise NotImplementedError


class CSVBackend(TrackingBackend):
    """Normalize runs from a task-local CSV file.

    Args:
        config: Project configuration used to resolve the default CSV path.
        path: Optional explicit CSV path.
    """

    STANDARD = {
        "id",
        "task_id",
        "experiment_id",
        "commit",
        "timestamp",
        "entrypoint",
        "url",
    }

    def __init__(self, config: HirakuConfig, path: Path | str | None = None) -> None:
        """Create a CSV adapter.

        Args:
            config: Project configuration.
            path: Optional CSV path override.
        """
        self.config = config
        self.path = Path(path).absolute() if path is not None else None

    def _read_runs(
        self, *, task_id: str, experiment_id: str | None = None
    ) -> list[Run]:
        from .data import Run

        path = self.path or self.config.tasks_dir / task_id / "runs.csv"
        if not path.exists():
            return []
        runs: list[Run] = []
        try:
            with path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or "id" not in reader.fieldnames:
                    raise BackendError(f"csv backend file {path} has no id column")
                if any(not name for name in reader.fieldnames) or len(
                    set(reader.fieldnames)
                ) != len(reader.fieldnames):
                    raise BackendError(
                        f"csv backend file {path} has invalid or duplicate columns"
                    )
                for row_number, row in enumerate(reader, start=2):
                    try:
                        if None in row:
                            raise ValueError("row has more values than columns")
                        run_id = row.get("id", "")
                        if not run_id:
                            raise ValueError("id is empty")
                        metrics = {
                            key.removeprefix("metric."): value
                            for key, value in row.items()
                            if key.startswith("metric.") and value != ""
                        }
                        params = {
                            key.removeprefix("param."): value
                            for key, value in row.items()
                            if key.startswith("param.") and value != ""
                        }
                        raw = {
                            key: value
                            for key, value in row.items()
                            if key not in self.STANDARD
                            and not key.startswith("metric.")
                            and not key.startswith("param.")
                        }
                        normalized = {
                            key: (value if value != "" else None)
                            for key, value in row.items()
                        }
                        run = Run(
                            id=run_id,
                            task_id=normalized.get("task_id"),
                            experiment_id=normalized.get("experiment_id"),
                            commit=normalized.get("commit"),
                            timestamp=self._timestamp(
                                normalized.get("timestamp"),
                                backend="csv",
                                context=f"{path} row {row_number}",
                            ),
                            entrypoint=normalized.get("entrypoint"),
                            url=normalized.get("url"),
                            metrics=metrics,
                            params=params,
                            raw=raw,
                        )
                    except (
                        ValueError,
                        TypeError,
                        ValidationError,
                        BackendError,
                    ) as exc:
                        if isinstance(exc, BackendError):
                            raise
                        raise BackendError(
                            f"csv backend malformed row in {path} "
                            f"at row {row_number}: {exc}"
                        ) from exc
                    runs.append(run)
        except BackendError:
            raise
        except (OSError, csv.Error, UnicodeError) as exc:
            raise BackendError(f"csv backend could not read {path}: {exc}") from exc
        return runs


class MLflowBackend(TrackingBackend):
    """Normalize native MLflow runs without importing MLflow eagerly.

    Args:
        config: Project configuration.
        tracking_uri: Optional MLflow tracking URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.
    """

    def __init__(
        self,
        config: HirakuConfig,
        *,
        tracking_uri: str | None = None,
        experiment_name: str | None = None,
        database: Path | None = None,
    ) -> None:
        """Create an MLflow adapter from validated settings.

        Args:
            config: Project configuration.
            tracking_uri: Optional remote tracking URI.
            experiment_name: Optional experiment name.
            database: Optional local database path.

        Raises:
            hiraku.errors.BackendError: If URI and database settings conflict.
        """
        self.config = config
        settings = MLflowBackendConfig(
            tracking_uri=tracking_uri,
            experiment_name=experiment_name,
            database=database,
        )
        self.tracking_uri = settings.tracking_uri or _mlflow_uri(
            config, settings.database
        )
        self.experiment_name = settings.experiment_name

    @staticmethod
    def _escape_filter(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _read_runs(
        self, *, task_id: str, experiment_id: str | None = None
    ) -> list[Run]:
        from .data import Run

        try:
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise BackendError(
                "mlflow backend requires the optional 'mlflow' dependency"
            ) from exc
        try:
            client = MlflowClient(tracking_uri=self.tracking_uri)
            experiment_name = self.experiment_name
            if experiment_name is None:
                from .data import load_task

                task = load_task(task_id, config=self.config)
                experiment_name = task.mlflow_experiment
            if experiment_name:
                experiment = client.get_experiment_by_name(str(experiment_name))
                if experiment is None:
                    return []
                experiment_ids = [experiment.experiment_id]
            else:
                experiment_ids = [
                    item.experiment_id for item in client.search_experiments()
                ]
            native_runs = client.search_runs(
                experiment_ids=experiment_ids,
                filter_string=(
                    f"tags.`hiraku.task_id` = '{self._escape_filter(task_id)}'"
                ),
            )
            runs: list[Run] = []
            for native in native_runs:
                info, data = native.info, native.data
                tags = dict(data.tags)
                native_task = tags.get("hiraku.task_id")
                native_experiment = tags.get("hiraku.experiment_id")
                known_tags = {
                    "hiraku.task_id",
                    "hiraku.experiment_id",
                    "hiraku.commit",
                    "hiraku.entrypoint",
                }
                raw = {
                    "status": info.status,
                    "artifact_uri": info.artifact_uri,
                    "experiment_id": info.experiment_id,
                    "tags": {
                        key: value
                        for key, value in tags.items()
                        if key not in known_tags
                    },
                }
                runs.append(
                    Run(
                        id=info.run_id,
                        task_id=native_task,
                        experiment_id=native_experiment,
                        commit=tags.get("hiraku.commit"),
                        entrypoint=tags.get("hiraku.entrypoint"),
                        timestamp=self._timestamp(
                            info.start_time,
                            backend="mlflow",
                            context=f"run {info.run_id}",
                        ),
                        url=None,
                        metrics=dict(data.metrics),
                        params=dict(data.params),
                        raw=raw,
                    )
                )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"mlflow backend query failed: {exc}") from exc
        return runs


def _mlflow_uri(config: HirakuConfig, database: Path | None = None) -> str:
    remote = config.mlflow_tracking_uri
    database = database or config.mlflow_tracking_db
    if remote and database:
        raise BackendError(
            "mlflow backend configuration cannot set both mlflow_tracking_uri "
            "and mlflow_tracking_db"
        )
    if remote:
        if not isinstance(remote, str):
            raise BackendError("mlflow backend tracking URI must be a string")
        return remote
    relative = Path(database or "mlflow.db")
    if relative.is_absolute() or ".." in relative.parts:
        raise BackendError(
            "mlflow backend database must be a safe project-relative path"
        )
    resolved = (config.root / relative).resolve(strict=False)
    try:
        resolved.relative_to(config.root.resolve())
    except ValueError as exc:
        raise BackendError(
            "mlflow backend database resolves outside project root"
        ) from exc
    return f"sqlite:///{resolved}"


def select_backend(task: Task, config: HirakuConfig) -> TrackingBackend:
    """Select a task override or the project's configured tracking backend."""
    specification = task.tracking_backend or config.tracking_backend
    parsed = _parse_backend_config(specification)
    if isinstance(parsed, CSVBackendConfig):
        return CSVBackend(config, path=parsed.path)
    return MLflowBackend(
        config,
        tracking_uri=parsed.tracking_uri,
        experiment_name=parsed.experiment_name,
        database=parsed.database,
    )


__all__ = [
    "BackendConfig",
    "CSVBackend",
    "CSVBackendConfig",
    "MLflowBackend",
    "MLflowBackendConfig",
    "TrackingBackend",
    "select_backend",
]
