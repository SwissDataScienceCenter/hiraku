"""
This package exposes a stable backend and a registry for tracking backends
with some pre-defined ones.

The registry allows registration of custom tracking backends for research runs.

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

    def _read_source_runs(self):
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


def select_backend(record, config):
    """Select the configured backend for a research record."""
    specification = record.resolve(config=config).values.get("tracking_backend")
    specification = specification or config.tracking_backend
    return backend_for(specification, config)


def list_runs_for_records(
    records,
    config,
    *,
    include_descendants=False,
    include_unattributed=False,
):
    """Query the source selected by a record, or group a subtree by source."""
    from collections import defaultdict

    records = list(records)
    if not records:
        return []
    if not include_descendants:
        record = records[0]
        adapter = select_backend(record, config)
        values = record.resolve(config=config).values
        experiment_name = values.get("mlflow_experiment")
        if isinstance(adapter, MLflowBackend):
            return adapter.list_all_recorded_runs(
                experiment_name=experiment_name,
                include_unattributed=include_unattributed,
            )
        return adapter.list_all_recorded_runs(include_unattributed=include_unattributed)

    groups = defaultdict(list)
    for record in records:
        values = record.resolve(config=config).values
        specification = values.get("tracking_backend") or config.tracking_backend
        experiment_name = values.get("mlflow_experiment")
        if isinstance(specification, dict):
            experiment_name = specification.get("experiment_name") or experiment_name
        key = (repr(specification), repr(experiment_name))
        groups[key].append((record, experiment_name))

    runs = []
    for entries in groups.values():
        record, experiment_name = entries[0]
        adapter = select_backend(record, config)
        record_ids = {item.id for item, _ in entries}
        if isinstance(adapter, MLflowBackend):
            source_runs = adapter.list_all_recorded_runs(
                experiment_name=experiment_name,
                include_unattributed=include_unattributed,
            )
        else:
            source_runs = adapter.list_all_recorded_runs(
                include_unattributed=include_unattributed
            )
        runs.extend(
            run
            for run in source_runs
            if run.record_id in record_ids
            or (include_unattributed and run.record_id is None)
        )
    return runs


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
    "list_runs_for_records",
    "register_backend",
    "select_backend",
]
