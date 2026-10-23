"""MLflow tracking backend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import HirakuConfig
from ..errors import BackendError
from .base import TrackingBackend

if TYPE_CHECKING:
    from ..run import Run


class MLflowBackendConfig(BaseModel):
    """Validated options for the MLflow tracking backend.

    Attributes:
        type: Backend discriminator; must be "mlflow".
        tracking_uri: Optional remote MLflow URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.
        ui_url: Optional absolute HTTP(S) base URL of the MLflow web UI.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["mlflow"] = "mlflow"
    tracking_uri: str | None = Field(default=None, min_length=1)
    experiment_name: str | None = Field(default=None, min_length=1)
    database: Path | None = None
    ui_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> MLflowBackendConfig:
        if self.tracking_uri and self.database:
            raise BackendError(
                "mlflow backend cannot set both tracking_uri and database"
            )
        if self.ui_url is not None and not _is_web_base(self.ui_url):
            raise BackendError(
                "mlflow ui_url must be an absolute HTTP(S) URL without "
                "credentials, query, or fragment"
            )
        return self


class MLflowBackend(TrackingBackend):
    """Normalize native MLflow runs without importing MLflow eagerly.

    Args:
        config: Project configuration.
        tracking_uri: Optional MLflow tracking URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.
        ui_url: Optional base URL of the MLflow web UI used for run links.
    """

    def __init__(
        self,
        config: HirakuConfig,
        *,
        tracking_uri: str | None = None,
        experiment_name: str | None = None,
        database: Path | None = None,
        ui_url: str | None = None,
    ) -> None:
        """Create an MLflow adapter from validated settings.

        Args:
            config: Project configuration.
            tracking_uri: Optional remote tracking URI.
            experiment_name: Optional experiment name.
            database: Optional local database path.
            ui_url: Optional web UI base URL. Defaults to an HTTP(S)
                ``tracking_uri``, which MLflow servers also serve the UI from.

        Raises:
            hiraku.errors.BackendError: If URI and database settings conflict
                or ``ui_url`` is not a plain HTTP(S) URL.
        """
        self.config = config
        settings = MLflowBackendConfig(
            tracking_uri=tracking_uri,
            experiment_name=experiment_name,
            database=database,
            ui_url=ui_url,
        )
        self.tracking_uri = settings.tracking_uri or _mlflow_uri(
            config, settings.database
        )
        self.experiment_name = settings.experiment_name
        # Credential-bearing tracking URIs stay usable but never become links.
        self.ui_url = settings.ui_url or (
            self.tracking_uri if _is_web_base(self.tracking_uri) else None
        )

    def _read_source_runs(self, **options: Any) -> list[Run]:
        from ..run import Run

        try:
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise BackendError(
                "mlflow backend requires the optional 'mlflow' dependency"
            ) from exc
        try:
            client = MlflowClient(tracking_uri=self.tracking_uri)
            if self.experiment_name:
                experiment = client.get_experiment_by_name(self.experiment_name)
                if experiment is None:
                    return []
                experiment_ids = [experiment.experiment_id]
            else:
                experiment_ids = []
                token = None
                while True:
                    page = client.search_experiments(page_token=token)
                    experiment_ids.extend(item.experiment_id for item in page)
                    token = getattr(page, "token", None)
                    if not token:
                        break
            if not experiment_ids:
                return []
            # MLflow returns at most one page of runs per search call.
            native_runs: list[Any] = []
            token = None
            while True:
                page = client.search_runs(
                    experiment_ids=experiment_ids, max_results=1000, page_token=token
                )
                native_runs.extend(page)
                token = getattr(page, "token", None)
                if not token:
                    break
            runs: list[Run] = []
            for native in native_runs:
                info, data = native.info, native.data
                tags = dict(data.tags)
                native_record = tags.get("hiraku.record_id")
                known_tags = {
                    "hiraku.record_id",
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
                        record_id=native_record,
                        commit=tags.get("hiraku.commit"),
                        entrypoint=tags.get("hiraku.entrypoint"),
                        timestamp=self._timestamp(
                            info.start_time,
                            backend="mlflow",
                            context=f"run {info.run_id}",
                        ),
                        duration=_duration(info.start_time, info.end_time),
                        url=self._run_url(info.experiment_id, info.run_id),
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

    def _run_url(self, experiment_id: str, run_id: str) -> str | None:
        if not self.ui_url:
            return None
        experiment = quote(str(experiment_id), safe="")
        run = quote(str(run_id), safe="")
        return f"{self.ui_url.rstrip('/')}/#/experiments/{experiment}/runs/{run}"


def _duration(start_time: int | None, end_time: int | None) -> float | None:
    """Return seconds between MLflow's millisecond times; ``None`` while running."""
    if start_time is None or end_time is None:
        return None
    return (end_time - start_time) / 1000


def _is_web_base(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _mlflow_uri(config: HirakuConfig, database: Path | None = None) -> str:
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
