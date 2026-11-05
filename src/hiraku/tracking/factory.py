"""Construction of built-in tracking backends from project configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from ..errors import BackendError
from ..project import Project
from .base import (
    BackendConfig,
    CSVBackendConfig,
    MLflowBackendConfig,
    WandbBackendConfig,
)
from .csv import CSVBackend
from .mlflow import MLflowBackend
from .wandb import WandbBackend


class TrackingBackendFactory:
    """Create adapters from validated project settings."""

    _config_adapter = TypeAdapter(BackendConfig)

    def __init__(self, project: Project) -> None:
        self.project = project

    @staticmethod
    def validate(
        specification: str | Mapping[str, Any],
    ) -> CSVBackendConfig | MLflowBackendConfig | WandbBackendConfig:
        """Parse and validate a built-in backend name or configuration mapping."""
        if isinstance(specification, str):
            raw: dict[str, Any] = {"type": specification}
        elif isinstance(specification, Mapping):
            raw = dict(specification)
            if "type" not in raw and "name" in raw:
                raw["type"] = raw.pop("name")
        else:
            raise BackendError("Tracking backend must be a name or mapping")

        try:
            return TrackingBackendFactory._config_adapter.validate_python(raw)
        except (PydanticValidationError, TypeError, ValueError) as exc:
            name = raw.get("type", raw.get("name"))
            raise BackendError(
                f"Invalid tracking backend configuration {name!r}: {exc}"
            ) from exc

    def create(
        self, raw_config: str | Mapping[str, Any]
    ) -> CSVBackend | MLflowBackend | WandbBackend:
        validated = self.validate(raw_config)
        if isinstance(validated, CSVBackendConfig):
            return CSVBackend(self.project, settings=validated)
        if isinstance(validated, MLflowBackendConfig):
            return MLflowBackend(self.project, settings=validated)
        if isinstance(validated, WandbBackendConfig):
            return WandbBackend(validated.project, settings=validated)
        raise BackendError(
            f"Unsupported tracking backend configuration: {type(validated)}"
        )
