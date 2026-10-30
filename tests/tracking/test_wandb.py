from __future__ import annotations

import sys
import types
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.tracking.wandb import WandbBackend, WandbBackendConfig


class FakeRun:
    id = "run-1"
    config = {
        "hiraku.record_id": "logistic",
        "hiraku.commit": "a" * 40,
        "hiraku.entrypoint": "train",
        "seed": 42,
    }
    summary = {"test/accuracy": 0.95}
    tags = ["baseline", "iris"]
    created_at = "2026-01-01T12:00:00Z"
    url = "https://wandb.test/run-1"
    state = "finished"
    entity = "team"
    project = "iris"
    notes = "baseline"
    metadata = {"python": "3.14"}


class FakeUnattributedRun(FakeRun):
    id = "run-unattributed"
    config = {"seed": 7}


class FakeTimedRun(FakeRun):
    summary = {"test/accuracy": 0.95, "_runtime": 12.5}


class FakeApi:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def runs(self, path):
        assert path == "team/iris"
        return [FakeRun(), FakeUnattributedRun()]


def test_wandb_config_accepts_common_mapping() -> None:
    config = WandbBackendConfig.model_validate(
        {"type": "wandb", "entity": "team", "project": "iris"}
    )
    assert config.project == "iris"


def test_wandb_backend_normalizes_and_filters_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "wandb", types.SimpleNamespace(Api=FakeApi))
    backend = WandbBackend(HirakuConfig.init(tmp_path), entity="team", project="iris")
    runs = backend.list_runs(record_id="logistic")
    assert len(runs) == 1
    run = runs[0]
    assert run.id == "run-1"
    assert run.params == {"seed": 42}
    assert run.metrics == {"test/accuracy": 0.95}
    assert run.raw["tags"] == ["baseline", "iris"]
    assert [item.id for item in backend.list_runs()] == ["run-1"]
    assert [item.id for item in backend.list_runs()] == ["run-1"]


def test_wandb_backend_reads_runtime_as_duration(tmp_path: Path) -> None:
    backend = object.__new__(WandbBackend)
    backend.config = HirakuConfig.init(tmp_path)
    assert backend._normalize_run(FakeTimedRun()).duration == 12.5
    assert backend._normalize_run(FakeRun()).duration is None
