"""Construction of built-in tracking backends from project configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from ..errors import BackendError
from ..project import Project
from .base import TrackingBackend
from .csv import CSVBackend, CSVBackendConfig
from .mlflow import MLflowBackend, MLflowBackendConfig
from .wandb import WandbBackend, WandbBackendConfig

BackendConfig = Annotated[
    CSVBackendConfig | MLflowBackendConfig | WandbBackendConfig,
    Field(discriminator="type"),
]
_BACKEND_CONFIG = TypeAdapter(BackendConfig)


def validate_backend_config(
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
        parsed = _BACKEND_CONFIG.validate_python(raw)
    except (PydanticValidationError, TypeError, ValueError) as exc:
        name = raw.get("type", raw.get("name"))
        raise BackendError(
            f"Invalid tracking backend configuration {name!r}: {exc}"
        ) from exc

    return parsed


def create_backend(
    specification: str | Mapping[str, Any], config: Project
) -> TrackingBackend:
    """Create a built-in backend from a name or validated configuration mapping."""
    parsed = validate_backend_config(specification)
    if isinstance(parsed, CSVBackendConfig):
        return CSVBackend(config, settings=parsed)
    if isinstance(parsed, MLflowBackendConfig):
        return MLflowBackend(config, settings=parsed)
    if isinstance(parsed, WandbBackendConfig):
        return WandbBackend(config, settings=parsed)
    raise BackendError(f"Unsupported tracking backend configuration: {type(parsed)}")
