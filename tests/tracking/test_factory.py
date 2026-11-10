from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.errors import BackendError
from hiraku.project import Project
from hiraku.tracking import TrackingBackendFactory
from hiraku.tracking.csv import CSVBackend


def test_factory_constructs_csv_backend_from_mapping(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    backend = TrackingBackendFactory(config).create({"type": "csv"})
    assert isinstance(backend, CSVBackend)


def test_factory_rejects_unknown_backend(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    with pytest.raises(BackendError, match="tracking backend configuration"):
        TrackingBackendFactory(config).create({"type": "json"})


def test_factory_rejects_unknown_backend_options(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    with pytest.raises(BackendError, match="Invalid tracking backend configuration"):
        TrackingBackendFactory(config).create({"type": "csv", "extra": True})
