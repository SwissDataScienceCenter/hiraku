"""Normalized tracked runs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import BackendError, ValidationError


class Run(BaseModel):
    """An immutable experimental run returned by a tracking backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)

    metrics: Mapping[str, Any] = Field(default_factory=dict)
    params: Mapping[str, Any] = Field(default_factory=dict)

    job_id: str | None = None
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

    @model_validator(mode="after")
    def normalize(self) -> Run:
        for name in ("metrics", "params", "raw"):
            value = getattr(self, name)
            object.__setattr__(self, name, MappingProxyType(dict(value)))

        if self.timestamp is not None:
            value = self.timestamp
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            object.__setattr__(self, "timestamp", value.astimezone(UTC))

        return self


@contextmanager
def track_run(*, backend: Literal["mlflow", "wandb"], **kwargs: Any):
    """Track a run with MLflow or W&B, adding available Hiraku attribution.

    Backend-specific keyword arguments are forwarded to MLflow's
    ``start_run`` or W&B's ``init`` function.
    """
    if backend == "mlflow":
        from .tracking.mlflow import track_run as backend_track_run
    elif backend == "wandb":
        from .tracking.wandb import track_run as backend_track_run
    else:
        raise BackendError(f"Unsupported tracking backend: {backend!r}")

    with backend_track_run(**kwargs) as run:
        yield run
