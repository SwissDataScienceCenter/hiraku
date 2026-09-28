"""Entrypoints, metric declarations, and normalized tracked runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ValidationError
from .records import Record, list_records, load_record

ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]


class Entrypoint(BaseModel):
    """A command and default named arguments."""

    model_config = ConfigDict(extra="forbid")
    command: list[str]
    args: dict[str, ArgumentValue] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def validate_args_mapping(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("Entrypoint args must be a mapping")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValidationError("Entrypoint command must be non-empty strings")
        return value


class Metric(BaseModel):
    """A declared metric and its preferred direction."""

    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


class Run(BaseModel):
    """An immutable run returned by a tracking backend."""

    model_config = ConfigDict(frozen=True, extra="allow")
    id: str
    metrics: Mapping[str, Any] = Field(default_factory=dict)
    params: Mapping[str, Any] = Field(default_factory=dict)
    record_id: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None
    entrypoint: str | None = None
    url: str | None = None
    raw: Mapping[str, Any] = Field(default_factory=dict, repr=False)

    @field_validator("id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not value:
            raise ValidationError("Run ID must be non-empty")
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


__all__ = ["Entrypoint", "Metric", "Record", "Run", "list_records", "load_record"]
