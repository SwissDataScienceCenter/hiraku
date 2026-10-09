"""MLflow tracking backend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

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

    def _read_source_runs(
        self,
        *,
        experiment_name: str | None = None,
        **options: Any,
    ) -> list[Run]:
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
                experiment_ids = [
                    item.experiment_id for item in client.search_experiments()
                ]
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
