"""
This package exposes a stable backend and a registry for tracking backends
with some pre-defined ones.

The registry allows for the registration of new custom tracking backends,
which can be used to track experiments and their results in a consistent manner.

To add a new backend follow these steps:
1. Create a new backend file (e.g., tracking/wandb.py)
2. Implement a new backend class that inherits from `TrackingBackend`.
3. Implement a new backend configuration class that inherits from `BaseModel`.
4. Register the new backend in the registry using `register_backend`.

Example of a new backend implementation (tracking/wandb.py):

```python
# tracking/wandb.py
from pydantic import BaseModel, ConfigDict
from typing import Literal

from .base import TrackingBackend
from ..errors import BackendError


class WandbBackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["wandb"] = "wandb"
    entity: str | None = None
    project: str
    api_key: str | None = None


class WandbBackend(TrackingBackend):
    def __init__(self, config, *, entity=None, project="", api_key=None):
        self.config = config
        self.entity = entity
        self.project = project
        self.api_key = api_key

    def _read_runs(self, *, task_id, experiment_id=None):
        try:
            import wandb
        except ImportError as exc:
            raise BackendError(
                "wandb backend requires the optional 'wandb' dependency"
            ) from exc

        api = wandb.Api()
        # TODO: Convert each fetched W&B run into Hiraku's Run model.
        return []
```
"""

from .base import TrackingBackend
from .csv import CSVBackend, CSVBackendConfig
from .mlflow import MLflowBackend, MLflowBackendConfig
from .registry import available_backends, backend_for, register_backend
from .wandb import WandbBackend, WandbBackendConfig

BackendConfig = CSVBackendConfig | MLflowBackendConfig | WandbBackendConfig


def select_backend(task, config):
    """Select the configured backend for a task."""
    specification = task.tracking_backend or config.tracking_backend
    return backend_for(specification, config)


__all__ = [
    "BackendConfig",
    "CSVBackend",
    "CSVBackendConfig",
    "MLflowBackend",
    "MLflowBackendConfig",
    "WandbBackend",
    "WandbBackendConfig",
    "TrackingBackend",
    "available_backends",
    "backend_for",
    "register_backend",
    "select_backend",
]
