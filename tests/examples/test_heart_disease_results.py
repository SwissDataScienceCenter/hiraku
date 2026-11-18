from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import Leaderboard


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
    assert [metric.direction for metric in Leaderboard.load(config).metrics] == [
        "minimize",
        "maximize",
    ]
    leaderboard = Leaderboard.load(config)
    runs = leaderboard.list_runs(config, record_id=forest.id)
    assert len(runs) == 3
    assert len(leaderboard.list_runs(config, record_id=forest.id)) == 3
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["leaderboard", "runs", "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 9
    training = runpy.run_path(str(project / "train.py"))
    training["append_run"](
        project / "runs.csv",
        {
            "id": "new-training-run",
            "record_id": forest.id,
            "hiraku_job_id": "external-job",
            "timestamp": "2026-01-02T00:00:00Z",
            "duration": "1.5",
            "param.model": "random_forest",
            "param.seed": "4",
            "param.validation_fraction": "0.2",
            "metric.val/log_loss": "0.4",
            "metric.val/accuracy": "0.8",
        },
    )
    runs = Leaderboard.load(config).list_runs(config)
    assert len(runs) == 10
    assert next(run for run in runs if run.id == "new-training-run").duration == 1.5


@pytest.mark.parametrize("include_job_id", [False, True])
def test_writer_rejects_other_csv_schemas_without_migrating(tmp_path, include_job_id):
    source = (
        Path(__file__).parents[2]
        / "src/hiraku/resources/examples/heart-disease/src/train.py"
    )
    training = runpy.run_path(str(source))
    fields = [field for field in training["RUN_FIELDS"] if field != "hiraku_job_id"]
    if include_job_id:
        fields.append("hiraku_job_id")
    path = tmp_path / "runs.csv"
    path.write_text(",".join(fields) + "\n")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Unexpected runs CSV header"):
        training["append_run"](path, {"id": "new-run"})
    assert path.read_bytes() == before
