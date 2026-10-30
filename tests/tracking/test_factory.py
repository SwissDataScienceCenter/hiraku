from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.tracking import create_backend
from hiraku.tracking.csv import CSVBackend


def test_factory_constructs_csv_backend_from_mapping(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = create_backend({"type": "csv"}, config)
    assert isinstance(backend, CSVBackend)


def test_factory_rejects_unknown_backend(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(BackendError, match="tracking backend configuration"):
        create_backend({"type": "json"}, config)


def test_factory_rejects_unknown_backend_options(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(BackendError, match="Invalid tracking backend configuration"):
        create_backend({"type": "csv", "extra": True}, config)
