from __future__ import annotations

from pathlib import Path

from hiraku.core.config import HirakuConfig
from hiraku.tracking import available_backends, backend_for
from hiraku.tracking.csv import CSVBackend


def test_builtin_backends_are_registered() -> None:
    assert available_backends() == ("csv", "mlflow", "wandb")


def test_registry_constructs_csv_backend_from_mapping(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = backend_for({"type": "csv"}, config)
    assert isinstance(backend, CSVBackend)
