"""Read-only tracking backend selection and CSV/MLflow adapters."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .config import HirakuConfig
from .errors import BackendError, ValidationError

if TYPE_CHECKING:
    from .data import Run, Task


def _validate_filters(task_id: str, experiment_id: str | None) -> None:
    if not isinstance(task_id, str) or not task_id:
        raise BackendError("Tracking queries require a non-empty task_id")
    if experiment_id is not None and (
        not isinstance(experiment_id, str) or not experiment_id
    ):
        raise BackendError("experiment_id must be a non-empty string or null")


class TrackingBackend(Protocol):
    """Portable read-only tracking backend interface."""

    def list_runs(
        self, *, task_id: str, experiment_id: str | None = None, **query: Any
    ) -> list[Run]: ...


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


def _apply_query(runs: list[Run], query: Mapping[str, Any], backend: str) -> list[Run]:
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


class CSVBackend:
    """Normalize runs from a task-local CSV file."""

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
        self.config = config
        self.path = Path(path).absolute() if path is not None else None

    def list_runs(
        self, *, task_id: str, experiment_id: str | None = None, **query: Any
    ) -> list[Run]:
        from .data import Run

        _validate_filters(task_id, experiment_id)
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
                            timestamp=_timestamp(
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
                    if run.task_id not in {None, task_id}:
                        continue
                    if experiment_id is not None and run.experiment_id != experiment_id:
                        continue
                    runs.append(run)
        except BackendError:
            raise
        except (OSError, csv.Error, UnicodeError) as exc:
            raise BackendError(f"csv backend could not read {path}: {exc}") from exc
        return _apply_query(runs, query, "csv")


class MLflowBackend:
    """Normalize native MLflow runs without importing MLflow at core import time."""

    def __init__(
        self,
        config: HirakuConfig,
        *,
        tracking_uri: str | None = None,
        experiment_name: str | None = None,
    ) -> None:
        self.config = config
        self.tracking_uri = tracking_uri or _mlflow_uri(config)
        self.experiment_name = experiment_name

    def list_runs(
        self, *, task_id: str, experiment_id: str | None = None, **query: Any
    ) -> list[Run]:
        from .data import Run

        _validate_filters(task_id, experiment_id)
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
                experiment_name = task.get("mlflow_experiment")
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
                filter_string=f"tags.`hiraku.task_id` = '{_escape_filter(task_id)}'",
            )
            runs: list[Run] = []
            for native in native_runs:
                info, data = native.info, native.data
                tags = dict(data.tags)
                native_task = tags.get("hiraku.task_id")
                native_experiment = tags.get("hiraku.experiment_id")
                if native_task != task_id:
                    continue
                if experiment_id is not None and native_experiment != experiment_id:
                    continue
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
                        timestamp=_timestamp(
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
        return _apply_query(runs, query, "mlflow")


def _escape_filter(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _mlflow_uri(config: HirakuConfig) -> str:
    remote = config._data.get("mlflow_tracking_uri")
    database = config._data.get("mlflow_tracking_db")
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
    options: dict[str, Any] = {}
    if isinstance(specification, Mapping):
        options = dict(specification)
        name = options.pop("name", options.pop("type", None))
    else:
        name = specification
    if name == "csv":
        path = options.pop("path", None)
        if options:
            raise BackendError(f"csv backend has unknown options: {sorted(options)}")
        return CSVBackend(config, path=path)
    if name == "mlflow":
        allowed = {"tracking_uri", "experiment_name"}
        unknown = set(options) - allowed
        if unknown:
            raise BackendError(f"mlflow backend has unknown options: {sorted(unknown)}")
        return MLflowBackend(config, **options)
    raise BackendError(f"Unknown tracking backend: {name!r}")


__all__ = ["CSVBackend", "MLflowBackend", "TrackingBackend", "select_backend"]
