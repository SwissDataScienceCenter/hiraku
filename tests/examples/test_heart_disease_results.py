from __future__ import annotations

import json
import runpy
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import TrackingBackendFactory


def test_populated_example_initializes_and_queries_runs(tmp_path: Path, monkeypatch):
    project = tmp_path.resolve() / "heart-disease-results"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "example",
            "create",
            "heart-disease",
            str(project),
            "--with-results",
        ],
    )
    assert result.exit_code == 0, result.output
    assert project.joinpath("train.py").is_file()
    assert project.joinpath("data/processed.cleveland.data").is_file()
    assert not project.joinpath("generate_results.py").exists()
    assert not project.joinpath("README.md").exists()
    config = Project.discover(project)
    forest = config.vault.load("heart-disease-prediction/random-forest")
    assert config.active_record_id == forest.id
    records = config.vault.list()
    assert {record.id: record.title for record in records} == {
        "heart-disease-prediction": "Heart disease prediction",
        "heart-disease-prediction/logistic-regression": "Logistic regression",
        "heart-disease-prediction/random-forest": "Random forest",
        "heart-disease-prediction/random-forest/validation-split": (
            "Validation split sensitivity"
        ),
    }
    assert forest.parent_id == config.vault.load("heart-disease-prediction").id
    assert (
        config.vault.load(
            "heart-disease-prediction/random-forest/validation-split"
        ).parent_id
        == forest.id
    )
    assert forest.metrics is None
    parent = config.vault.load(forest.parent_id)
    assert [metric.direction for metric in parent.metrics] == ["minimize", "maximize"]
    backend = TrackingBackendFactory(config).create(config.tracking_backend)
    runs = backend.list_runs(record_id=forest.id)
    assert len(runs) == 3
    assert len(backend.list_runs(record_id=forest.id)) == 3
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["runs", "--record", forest.id, "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 9
    training = runpy.run_path(str(project / "train.py"))
    training["append_run"](
        project / "runs.csv",
        {
            "id": "new-training-run",
            "record_id": forest.id,
            "timestamp": "2026-01-02T00:00:00Z",
            "duration": "1.5",
            "param.model": "random_forest",
            "param.seed": "4",
            "param.validation_fraction": "0.2",
            "metric.val/log_loss": "0.4",
            "metric.val/accuracy": "0.8",
            "commit": "",
            "git_state": "",
        },
    )
    runs = TrackingBackendFactory(config).create(config.tracking_backend).list_runs()
    assert len(runs) == 10
    assert next(run for run in runs if run.id == "new-training-run").duration == 1.5
