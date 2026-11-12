from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ValidationError
from hiraku.project import Project
from hiraku.tracking import Leaderboard, WandbBackend, WandbBackendConfig


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
    backend = WandbBackend("iris", entity="team")
    runs = backend.list_runs(record_id="logistic")
    assert len(runs) == 1
    run = runs[0]
    assert run.id == "run-1"
    assert "entrypoint" not in run.raw
    assert run.params == {"seed": "42"}
    assert run.metrics == {"test/accuracy": 0.95}
    assert run.raw["tags"] == ["baseline", "iris"]
    assert [item.id for item in backend.list_runs()] == ["run-1"]
    assert [item.id for item in backend.list_runs(include_unattributed=True)] == [
        "run-1",
        "run-unattributed",
    ]


def test_wandb_leaderboard_requires_explicit_project(tmp_path: Path) -> None:
    project = Project.create(tmp_path, install_skill=False)
    for source in ({"type": "wandb"}, {"type": "wandb", "project": ""}):
        with pytest.raises(ValidationError, match="Invalid leaderboard"):
            Leaderboard.create(project, "invalid", source)
    assert list(project.leaderboards_dir.iterdir()) == []


def test_wandb_runs_cli_uses_active_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "wandb", types.SimpleNamespace(Api=FakeApi))
    monkeypatch.chdir(tmp_path)
    project = Project.create(tmp_path, install_skill=False)
    (project.leaderboards_dir / "wandb.yml").write_text(
        "source: {type: wandb, entity: team, project: iris}\n"
    )
    project.save()
    record = project.vault.create("logistic/logistic", type="task", title="logistic")
    project.set_active_record(record)
    monkeypatch.setattr(FakeRun, "config", {"hiraku.record_id": record.id})

    result = CliRunner().invoke(main, ["leaderboard", "runs", "--json"])

    assert result.exit_code == 0, result.output
    assert '"run-1"' in result.output


def test_wandb_backend_reads_runtime_as_duration(tmp_path: Path) -> None:
    backend = object.__new__(WandbBackend)
    backend.project = Project.create(tmp_path)
    assert backend._normalize_run(FakeTimedRun()).duration == 12.5
    assert backend._normalize_run(FakeRun()).duration is None
