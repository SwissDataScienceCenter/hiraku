"""MLflow tracking backend."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlsplit

from ..errors import BackendError
from ..project import Project
from .base import BackendOperations, MLflowBackendConfig, Run, _validate_mlflow_url


class MLflowBackend(BackendOperations):
    """Normalize native MLflow runs without importing MLflow eagerly.

    Args:
        project: Hiraku project.
        settings: Pre-validated MLflow settings supplied by a leaderboard.
        tracking_uri: HTTP(S) server URL; defaults to MLFLOW_TRACKING_URI.
        experiment_name: Optional MLflow experiment name.
        ui_url: Optional base URL of the MLflow web UI used for run links.
    """

    def __init__(
        self,
        project: Project,
        *,
        settings: MLflowBackendConfig | None = None,
        tracking_uri: str | None = None,
        experiment_name: str | None = None,
        ui_url: str | None = None,
    ) -> None:
        """Create an MLflow adapter from validated settings.

        Args:
            project: Hiraku project.
            settings: Pre-validated MLflow settings supplied by a leaderboard.
            tracking_uri: HTTP(S) server URL; defaults to MLFLOW_TRACKING_URI.
            experiment_name: Experiment name; otherwise use MLflow environment
                variables, then the project directory name.
            ui_url: Optional web UI base URL. Defaults to an HTTP(S)
                ``tracking_uri``, which MLflow servers also serve the UI from.

        Raises:
            hiraku.errors.BackendError: If the tracking server URL is missing
                or a connection setting is invalid.
        """
        if settings is None:
            settings = MLflowBackendConfig(
                tracking_uri=tracking_uri,
                experiment_name=experiment_name,
                ui_url=ui_url,
            )
        elif any(
            value is not None for value in (tracking_uri, experiment_name, ui_url)
        ):
            raise BackendError("Pass MLflow backend settings or options, not both")
        if not isinstance(settings, MLflowBackendConfig):
            raise BackendError("MLflow backend settings must be MLflowBackendConfig")
        self.project = project
        tracking_uri = settings.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        if not tracking_uri:
            raise BackendError(
                "mlflow backend requires tracking_uri or MLFLOW_TRACKING_URI "
                "pointing to an HTTP(S) tracking server"
            )
        self.tracking_uri = _validate_mlflow_url(tracking_uri)
        configured_experiment_id = (
            os.environ.get("MLFLOW_EXPERIMENT_ID") or None
            if settings.experiment_name is None
            else None
        )
        self.experiment_name = (
            settings.experiment_name
            or os.environ.get("MLFLOW_EXPERIMENT_NAME")
            or (None if configured_experiment_id else project.root.name)
        )
        # Credential-bearing tracking URIs stay usable but never become links.
        parsed = urlsplit(self.tracking_uri)
        self.ui_url = settings.ui_url or (
            self.tracking_uri
            if parsed.username is None and parsed.password is None
            else None
        )

        try:
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise BackendError(
                "mlflow backend requires the optional 'mlflow' dependency"
            ) from exc

        try:
            self._mlflow_client = MlflowClient(tracking_uri=self.tracking_uri)
            self._experiment_id: str | None = None
            if self.experiment_name:
                experiment = self._mlflow_client.get_experiment_by_name(
                    self.experiment_name
                )
                if experiment is not None:
                    self._experiment_id = experiment.experiment_id
                if (
                    configured_experiment_id is not None
                    and self._experiment_id != configured_experiment_id
                ):
                    raise BackendError(
                        "MLFLOW_EXPERIMENT_NAME and MLFLOW_EXPERIMENT_ID "
                        "refer to different experiments"
                    )
            elif configured_experiment_id is not None:
                self._experiment_id = self._mlflow_client.get_experiment(
                    configured_experiment_id
                ).experiment_id
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"mlflow backend could not initialize tracking: {exc}"
            ) from exc

    def set_tag(self, run_id: str, key: str, value: str) -> None:
        """Set a tag on a run by ID on the configured MLflow server."""
        try:
            self._mlflow_client.set_tag(run_id, key, value)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"mlflow backend could not set the run tag: {exc}"
            ) from exc

    def _read_source_runs(self) -> list[Run]:
        try:
            client = self._mlflow_client
            if self._experiment_id is not None:
                experiment_ids = [self._experiment_id]
            elif self.experiment_name:
                return []
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
                    "hiraku.job_id",
                    "hiraku.record_id",
                    "hiraku.commit",
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
                        job_id=tags.get("hiraku.job_id"),
                        record_id=native_record,
                        commit=tags.get("hiraku.commit"),
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
