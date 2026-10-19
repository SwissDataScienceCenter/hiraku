from __future__ import annotations

from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.tracking import available_backends, create_backend
from hiraku.tracking.csv import CSVBackend


def test_builtin_backends_are_registered() -> None:
    assert available_backends() == ("csv", "mlflow", "snakemake", "wandb")


def test_registry_constructs_csv_backend_from_mapping(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = create_backend({"type": "csv"}, config)
    assert isinstance(backend, CSVBackend)
