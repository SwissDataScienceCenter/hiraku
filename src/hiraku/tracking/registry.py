"""Registry for selecting tracking backend implementations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..config import HirakuConfig
from ..errors import BackendError
from .base import TrackingBackend
from .csv import CSVBackend, CSVBackendConfig
from .mlflow import MLflowBackend, MLflowBackendConfig
from .snakemake import SnakemakeBackend, SnakemakeBackendConfig
from .wandb import WandbBackend, WandbBackendConfig

BackendFactory = Callable[[HirakuConfig, Any], TrackingBackend]


def _csv_factory(config: HirakuConfig, parsed: CSVBackendConfig) -> TrackingBackend:
    return CSVBackend(config, path=parsed.path)


def _mlflow_factory(config: HirakuConfig, parsed: Any) -> TrackingBackend:
    return MLflowBackend(
        config,
        tracking_uri=parsed.tracking_uri,
        experiment_name=parsed.experiment_name,
        database=parsed.database,
        ui_url=parsed.ui_url,
    )


def _wandb_factory(config: HirakuConfig, parsed: WandbBackendConfig) -> TrackingBackend:
    return WandbBackend(config, **parsed.model_dump(exclude={"type"}))


def _snakemake_factory(
    config: HirakuConfig, parsed: SnakemakeBackendConfig
) -> TrackingBackend:
    return SnakemakeBackend(config, path=parsed.path)


_BACKENDS: dict[str, tuple[type[Any], BackendFactory]] = {
    "csv": (CSVBackendConfig, _csv_factory),
    "mlflow": (MLflowBackendConfig, _mlflow_factory),
    "wandb": (WandbBackendConfig, _wandb_factory),
    "snakemake": (SnakemakeBackendConfig, _snakemake_factory),
}


def register_backend(
    name: str,
    config_type: type[Any],
    factory: BackendFactory,
) -> None:
    """Register a backend factory under a configuration name.

    Args:
        name: Non-empty backend name.
        config_type: Pydantic configuration model for the backend.
        factory: Callable creating a backend from config and parsed settings.

    Raises:
        BackendError: If the name or factory is invalid, or already registered.
    """
    if not isinstance(name, str) or not name.strip():
        raise BackendError("Tracking backend name must be non-empty")
    if name in _BACKENDS:
        raise BackendError(f"Tracking backend already registered: {name!r}")
    _BACKENDS[name] = (config_type, factory)


def available_backends() -> tuple[str, ...]:
    """Return registered backend names in stable order."""
    return tuple(sorted(_BACKENDS))


def create_backend(
    specification: str | Mapping[str, Any], config: HirakuConfig
) -> TrackingBackend:
    """Construct a backend from a name or validated configuration mapping."""
    if isinstance(specification, str):
        raw: dict[str, Any] = {"type": specification}
    elif isinstance(specification, Mapping):
        raw = dict(specification)
        if "type" not in raw and "name" in raw:
            raw["type"] = raw.pop("name")
    else:
        raise BackendError("Tracking backend must be a name or mapping")
    name = raw.get("type")
    entry = _BACKENDS.get(name)
    if entry is None:
        raise BackendError(f"Unknown tracking backend: {name!r}")
    try:
        parsed = entry[0].model_validate(raw)
    except Exception as exc:
        raise BackendError(
            f"Invalid {name!r} tracking backend configuration: {exc}"
        ) from exc
    return entry[1](config, parsed)
