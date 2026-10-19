"""Normalized tracked runs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ValidationError


class Run(BaseModel):
    """An immutable run returned by a tracking backend.

    ``timestamp`` is when the run started and ``duration`` its wall-clock
    length in seconds; either is ``None`` when the backend does not report it.
    """

    model_config = ConfigDict(frozen=True, extra="allow")
    id: str
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    params: Mapping[str, Any] = Field(default_factory=dict)
    record_id: str | None = None
    job_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    duration: float | None = None
    entrypoint: str | None = None
    url: str | None = None
    raw: Mapping[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not value:
            raise ValidationError("Run ID must be non-empty")
        return value

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
        from types import MappingProxyType

        for name in ("metrics", "params", "raw"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValidationError(f"Run {name} must be a mapping")
            object.__setattr__(self, name, MappingProxyType(dict(value)))
        if self.timestamp is not None:
            value = self.timestamp
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            object.__setattr__(self, "timestamp", value.astimezone(UTC))
        return self
