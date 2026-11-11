"""Shared tracking backend and query normalization."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..errors import (
    BackendError,
    ValidationError,
)


class Run(BaseModel):
    """
    Immutable normalized run returned by tracking backends.

    Attributes:
        id: Unique run ID.
        job_id: Optional Hiraku execution attempt ID logged by the script.
        metrics: Mapping of metric names to float values.
        params: Mapping of parameter names to string values.
        record_id: Optional bundle-relative concept path associated with the run.
        commit: Optional commit hash reported by the tracking source.
        timestamp: Optional run start time as a timezone-aware UTC datetime.
        duration: Optional run duration in seconds; ``None`` while running.
        url: Optional URL to the run in the backend's UI.
        raw: Backend-specific public metadata for the run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1)
    job_id: str | None = None
    metrics: Mapping[str, float] = Field(default_factory=dict)
    params: Mapping[str, str] = Field(default_factory=dict)
    record_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    duration: float | None = None
    url: str | None = None
    raw: Mapping[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, value: float | None) -> float | None:
        if value is not None and not (math.isfinite(value) and value >= 0):
            raise ValidationError(
                f"Run duration must be a non-negative number of seconds: {value!r}"
            )
        return value

    @field_validator("metrics", "params", "raw", mode="after")
    @classmethod
    def snapshot_mapping(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(dict(value))

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class TrackingBackend(Protocol):
    """Structural contract implemented by run tracking adapters."""

    backend_name: str

    def read_runs(self) -> list[Run]: ...

    def list_runs(
        self,
        *,
        record_id: str | None = None,
        include_unattributed: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[Run]: ...


class CSVBackendConfig(BaseModel):
    """Settings for reading runs from a CSV file.

    Attributes:
        type: Discriminator identifying the CSV backend.
        path: Required CSV path, relative to the project root or absolute.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["csv"] = "csv"
    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("CSV path must not be blank")
        return value


class MLflowBackendConfig(BaseModel):
    """Connection and experiment settings for MLflow tracking.

    Attributes:
        type: Discriminator identifying the MLflow backend.
        tracking_uri: Optional MLflow tracking server URI.
        experiment_name: Optional experiment name to query.
        database: Optional local MLflow database path.
        ui_url: Optional base URL used when constructing run links.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["mlflow"] = "mlflow"
    tracking_uri: str | None = Field(default=None, min_length=1)
    experiment_name: str | None = Field(default=None, min_length=1)
    database: Path | None = None
    ui_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> MLflowBackendConfig:
        from .mlflow import _is_web_base

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


class WandbBackendConfig(BaseModel):
    """Settings for querying a Weights & Biases project.

    Attributes:
        type: Discriminator identifying the W&B backend.
        entity: Optional W&B account or team name.
        project: W&B project name.
        id: Optional run ID to select.
        experiment_name: Optional experiment name.
        notes: Optional run notes filter.
        tags: Optional run tags filter.
        group: Optional run group filter.
        job_type: Optional run job type filter.
        mode: W&B connection mode.
        api_key: Optional API key; defaults to ``WANDB_API_KEY``.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["wandb"] = "wandb"
    entity: str | None = None
    project: str = Field(min_length=1)
    id: str | None = None
    experiment_name: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    tags: list[str] | None = None
    group: str | None = None
    job_type: str | None = None
    mode: Literal["online", "offline"] = "online"
    api_key: str | None = Field(
        default_factory=lambda: os.environ.get("WANDB_API_KEY"), exclude=True
    )


BackendConfig = Annotated[
    CSVBackendConfig | MLflowBackendConfig | WandbBackendConfig,
    Field(discriminator="type"),
]


def validate_query(
    *,
    record_id: str | None = None,
    include_unattributed: bool = False,
    sort: str | None = None,
    ascending: bool | None = None,
    limit: int | None = None,
) -> None:
    """Validate shared tracking query arguments before reading a source."""
    if record_id is not None and (not isinstance(record_id, str) or not record_id):
        raise BackendError("Tracking queries require a non-empty record_id")
    if not isinstance(include_unattributed, bool):
        raise BackendError("include_unattributed must be a boolean")
    if sort is not None and (not isinstance(sort, str) or not sort):
        raise BackendError("Tracking query sort must be a non-empty metric name")
    if ascending is not None and not isinstance(ascending, bool):
        raise BackendError("Tracking query ascending must be a boolean")
    if sort is None and ascending is not None:
        raise BackendError("Tracking query ascending requires a sort field")
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
    ):
        raise BackendError("Tracking query limit must be a non-negative integer")


def query_runs(
    runs: list[Run],
    *,
    record_id: str | None = None,
    include_unattributed: bool = False,
    sort: str | None = None,
    ascending: bool | None = None,
    limit: int | None = None,
) -> list[Run]:
    """Filter and order normalized runs after attribution; validate inputs first."""
    result = [
        run
        for run in runs
        if (include_unattributed or run.record_id)
        and (record_id is None or run.record_id == record_id)
    ]
    if sort is not None:
        ranked = [run for run in result if sort in run.metrics]
        unranked = [run for run in result if sort not in run.metrics]
        ranked.sort(key=lambda run: run.id)
        ranked.sort(key=lambda run: run.metrics[sort], reverse=ascending is False)
        result = [*ranked, *sorted(unranked, key=lambda run: run.id)]
    else:
        result.sort(
            key=lambda run: (
                run.timestamp is None,
                run.timestamp or datetime.max.replace(tzinfo=UTC),
                run.id,
            )
        )
    if limit is not None:
        result = result[:limit]
    return result


class BackendOperations:
    """Shared query behavior for built-in tracking adapters."""

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

    def list_runs(
        self,
        *,
        record_id: str | None = None,
        include_unattributed: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """Return normalized attributed runs by default.

        Args:
            record_id: Optional record ID to match exactly.
            include_unattributed: Include runs without a record ID. Defaults to false.
            sort: Plain metric name. Runs missing that metric follow, ordered by ID.
            ascending: Sort order; requires ``sort``. Defaults to ascending.
            limit: Maximum number of returned runs. Without ``sort``, runs are
                ordered by timestamp, with missing timestamps last.

        Returns:
            A list of normalized Run objects matching the specified filters and query.
        """
        validate_query(
            record_id=record_id,
            include_unattributed=include_unattributed,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )
        return query_runs(
            self.read_runs(),
            record_id=record_id,
            include_unattributed=include_unattributed,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )

    def read_runs(self) -> list[Run]:
        """Read normalized traces without filtering or Job enrichment."""
        return self._read_source_runs()

    def _read_source_runs(self) -> list[Run]:
        """Read all runs from the configured source once."""
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        return type(self).__name__.removesuffix("Backend").lower()
