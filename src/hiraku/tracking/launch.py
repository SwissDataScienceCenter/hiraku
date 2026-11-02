"""Tracking backend setup for launched commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import HirakuConfig
from .csv import CSVBackendConfig
from .factory import validate_backend_config
from .mlflow import MLflowBackend, MLflowBackendConfig
from .wandb import WandbBackendConfig


@dataclass(frozen=True)
class LaunchContext:
    """Git and record context supplied to tracking launch setup."""

    project_root: Path
    record_id: str
    commit: str | None
    git_state: str


class TrackingLaunchSetup(Protocol):
    """Prepare backend-specific environment for a launched command."""

    def environment(
        self,
        context: LaunchContext,
        inherited: Mapping[str, str],
    ) -> dict[str, str]:
        """Return backend environment additions, preserving user overrides."""


@dataclass(frozen=True)
class CSVLaunchSetup:
    """Prepare attribution for CSV writers in the child process."""

    def environment(
        self,
        context: LaunchContext,
        inherited: Mapping[str, str],
    ) -> dict[str, str]:
        del inherited
        return {"HIRAKU_COMMIT": context.commit or ""}


@dataclass(frozen=True)
class MLflowLaunchSetup:
    """Prepare MLflow tracking defaults for the child process."""

    config: HirakuConfig
    settings: MLflowBackendConfig

    def environment(
        self,
        context: LaunchContext,
        inherited: Mapping[str, str],
    ) -> dict[str, str]:
        del context
        backend = MLflowBackend(self.config, settings=self.settings)
        values = {
            "MLFLOW_TRACKING_URI": backend.tracking_uri,
            "MLFLOW_EXPERIMENT_NAME": backend.experiment_name or self.config.root.name,
        }
        return {key: value for key, value in values.items() if key not in inherited}


@dataclass(frozen=True)
class WandbLaunchSetup:
    """Prepare the W&B project default for the child process."""

    config: HirakuConfig
    settings: WandbBackendConfig

    def environment(
        self,
        context: LaunchContext,
        inherited: Mapping[str, str],
    ) -> dict[str, str]:
        del context
        project = self.settings.project or self.config.root.name
        if project and "WANDB_PROJECT" not in inherited:
            return {"WANDB_PROJECT": project}
        return {}


def create_launch_setup(
    config: HirakuConfig,
) -> TrackingLaunchSetup:
    """Create launch setup for the project's configured tracking backend."""
    settings = validate_backend_config(config.tracking_backend)
    if isinstance(settings, CSVBackendConfig):
        return CSVLaunchSetup()
    if isinstance(settings, MLflowBackendConfig):
        return MLflowLaunchSetup(config, settings)
    if isinstance(settings, WandbBackendConfig):
        return WandbLaunchSetup(config, settings)
    raise TypeError(f"Unsupported tracking launch settings: {type(settings)}")


def environment_for_launch(
    config: HirakuConfig,
    context: LaunchContext,
    *,
    inherited: Mapping[str, str],
) -> dict[str, str]:
    """Return backend-specific environment additions for a command launch."""
    return create_launch_setup(config).environment(context, inherited)
