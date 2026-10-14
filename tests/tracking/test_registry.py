from __future__ import annotations

from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.tracking import available_backends, create_backend
from hiraku.tracking.csv import CSVBackend
from hiraku.tracking.mlflow import MLflowBackend


def test_builtin_backends_are_registered() -> None:
    assert available_backends() == ("csv", "mlflow", "wandb")


def test_registry_constructs_csv_backend_from_mapping(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = create_backend({"type": "csv"}, config)
    assert isinstance(backend, CSVBackend)


def test_registry_forwards_mlflow_ui_url(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = create_backend(
        {"type": "mlflow", "database": "mlflow.db", "ui_url": "https://ui.example"},
        config,
    )
    assert isinstance(backend, MLflowBackend)
    assert backend.ui_url == "https://ui.example"
