"""MLflow tracking backend."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import HirakuConfig
from ..errors import BackendError
from .base import TrackingBackend

if TYPE_CHECKING:
    from ..run import Run


def _validate_ui_url(value: str) -> str:
    """Validate an MLflow web UI base URL."""
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise BackendError("mlflow ui_url must be an absolute HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BackendError("mlflow ui_url must be an absolute HTTP(S) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise BackendError("mlflow ui_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise BackendError("mlflow ui_url must not contain a query or fragment")
    return value


def _resolve_ui_url(tracking_uri: str | None, ui_url: str | None) -> str | None:
    """Return the UI base, preferring an explicit URL over the tracking URI."""
    if ui_url is not None:
        return _validate_ui_url(ui_url)
    if tracking_uri and tracking_uri.startswith(("http://", "https://")):
        try:
            return _validate_ui_url(tracking_uri)
        except BackendError:
            # Credential-bearing or malformed tracking URIs remain usable for
            # queries but must never be rendered as dashboard links.
            return None
    return None


class MLflowBackendConfig(BaseModel):
    """Validated options for the MLflow tracking backend.

    Attributes:
        type: Backend discriminator; must be "mlflow".
        tracking_uri: Optional remote MLflow URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.
        ui_url: Optional absolute HTTP(S) base URL for run links.
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
        if self.ui_url is not None:
            _validate_ui_url(self.ui_url)
        return self


class MLflowBackend(TrackingBackend):
    """Normalize native MLflow runs without importing MLflow eagerly.

    Args:
        config: Project configuration.
        tracking_uri: Optional MLflow tracking URI.
        experiment_name: Optional MLflow experiment name.
        database: Optional project-relative SQLite database path.
        ui_url: Optional absolute HTTP(S) base URL for run links.
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
            ui_url: Optional absolute HTTP(S) base URL for run links.

        Raises:
            hiraku.errors.BackendError: If URI and database settings conflict,
                or the UI URL is not a credential-free absolute HTTP(S) URL.
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
        self.ui_url = _resolve_ui_url(self.tracking_uri, settings.ui_url)

    def _read_source_runs(
        self,
        *,
        experiment_name: str | None = None,
        read_only: bool = False,
        **options: Any,
    ) -> list[Run]:
        if read_only:
            with self._read_only_uri() as uri:
                reader = MLflowBackend(
                    self.config,
                    tracking_uri=uri,
                    experiment_name=self.experiment_name,
                    ui_url=self.ui_url,
                )
                return reader._read_source_runs(experiment_name=experiment_name)
        from ..run import Run

        try:
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise BackendError(
                "mlflow backend requires the optional 'mlflow' dependency"
            ) from exc
        try:
            client = MlflowClient(tracking_uri=self.tracking_uri)
            experiment_name = experiment_name or self.experiment_name
            if experiment_name:
                experiment = client.get_experiment_by_name(str(experiment_name))
                if experiment is None:
                    return []
                experiment_ids = [experiment.experiment_id]
            else:
                experiment_ids = []
                experiment_token = None
                while True:
                    experiments = client.search_experiments(page_token=experiment_token)
                    experiment_ids.extend(item.experiment_id for item in experiments)
                    experiment_token = getattr(experiments, "token", None)
                    if not experiment_token:
                        break
            if not experiment_ids:
                return []
            native_runs: list[Any] = []
            page_token: str | None = None
            while True:
                page = client.search_runs(
                    experiment_ids=experiment_ids,
                    max_results=1000,
                    page_token=page_token,
                )
                native_runs.extend(page)
                page_token = page.token
                if not page_token:
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
        """Build a deep link for one native run, or ``None`` without a UI base."""
        if not self.ui_url:
            return None
        base = self.ui_url.rstrip("/")
        experiment = quote(str(experiment_id), safe="")
        run = quote(str(run_id), safe="")
        return f"{base}/#/experiments/{experiment}/runs/{run}"

    @contextmanager
    def _read_only_uri(self) -> Iterator[str]:
        """Isolate SQLite initialization/migrations from the inspected project."""
        from sqlalchemy.engine import make_url

        if self.tracking_uri.startswith(("http://", "https://", "databricks")):
            yield self.tracking_uri
            return
        if not self.tracking_uri.startswith("sqlite:"):
            raise BackendError(
                "Dashboard supports SQLite snapshots or remote MLflow servers"
            )
        url = make_url(self.tracking_uri)
        if not url.database or url.database == ":memory:":
            raise BackendError("Dashboard requires an existing MLflow database")
        path = Path(url.database).resolve()
        if not path.is_file():
            raise BackendError("MLflow database does not exist")
        with tempfile.TemporaryDirectory(prefix="hiraku-mlflow-") as directory:
            copy = Path(directory) / "snapshot.db"
            source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            target = sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            yield f"sqlite:///{copy}"


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
