# Track with a custom backend

A custom backend reads an existing tracking service or file format and returns Hiraku `Run` objects. It uses the same query interface as the built-in backends.

## Install and configure

The steps to implement a custom backend are:

1. Implement a Pydantic configuration model,
2. Define a `TrackingBackend` subclass,
3. Specify a factory function.

The subclass only needs to implement `_read_runs`; filtering, sorting, and limits are provided by the base class.

```python
# my_tracker.py
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from hiraku.config import HirakuConfig
from hiraku.data import Run
from hiraku.errors import BackendError
from hiraku.tracking import TrackingBackend, register_backend


class JsonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["json"] = "json"
    path: Path


class JsonBackend(TrackingBackend):
    def __init__(self, config: HirakuConfig, *, path: Path) -> None:
        self.config = config
        self.path = path

    def _read_runs(
        self, *, task_id: str, experiment_id: str | None = None
    ) -> list[Run]:
        try:
            rows: list[dict[str, Any]] = json.loads(
                self.path.read_text(encoding="utf-8")
            )
            return [Run.model_validate(row) for row in rows]
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BackendError(f"Could not read {self.path}: {exc}") from exc


def json_factory(config: HirakuConfig, parsed: JsonConfig) -> JsonBackend:
    path = parsed.path if parsed.path.is_absolute() else config.root / parsed.path
    return JsonBackend(config, path=path)


register_backend("json", JsonConfig, json_factory)
```

Import the module before selecting the backend in the same Python process:

```python
import my_tracker
```

The current CLI does not discover arbitrary project modules. Use the Python API for a project-local backend, or package/import the backend from the application startup that invokes the CLI.

Configure the task with the common backend mapping:

```bash
hiraku task update my-task \
  --set 'tracking_backend={type: json, path: tracking/runs.json}'
```

## Log from your script

The custom service must store records that can be converted to `Run`:

```json
[
  {
    "id": "run-001",
    "task_id": "my-task",
    "experiment_id": "baseline",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "timestamp": "2026-01-01T12:00:00Z",
    "entrypoint": "evaluate",
    "params": {"seed": 42},
    "metrics": {"val/accuracy": 0.84}
  }
]
```

Use `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, and `HIRAKU_ENTRYPOINT` when
writing attribution. Interpret `HIRAKU_COMMIT` together with
`HIRAKU_GIT_STATE`: a dirty launch reports its current base commit but not an
immutable source snapshot. Keep credentials outside `hiraku.yml`.

## Inspect

From Python:

```python
import my_tracker

from hiraku import HirakuConfig
from hiraku.data import load_task

config = HirakuConfig.discover()
task = load_task("my-task", config=config)
runs = task.runs(config=config, sort="val/accuracy", ascending=False)
```

When the backend is loaded by the CLI application, use:

```bash
hiraku runs --task my-task --sort val/accuracy --descending
```
