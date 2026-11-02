from __future__ import annotations

import json
import runpy
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records, open_workspace, resolve_metrics


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
    config = HirakuConfig.discover(project)
    assert config.active_record_id == "random-forest"
    records = list_records(open_workspace(config))
    assert len(records) == 4
    forest = Record.load(open_workspace(config), "random-forest")
    assert forest.parent_id == "heart-disease"
    assert Record.load(open_workspace(config), "forest-tuning").parent_id == forest.id
    assert forest.metrics is None
    assert [m.direction for m in resolve_metrics(forest, open_workspace(config))] == [
        "minimize",
        "maximize",
    ]
    runs = forest.resolve_tracking_backend(config=config).list_runs()
    assert len(runs) == 9
    assert [run.record_id for run in runs][:3] == ["logistic-baseline"] * 3
    assert len(forest.runs(config=config)) == 3
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
    runs = forest.resolve_tracking_backend(config=config).list_runs()
    assert len(runs) == 10
    assert next(run for run in runs if run.id == "new-training-run").duration == 1.5
