from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import Leaderboard

EXPECTED_METRICS = {
    "logistic-regression": (0.32254890, 0.85526316),
    "random-forest": (0.35226763, 0.86842105),
}


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_heart_disease_record_workflow(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "heart-disease"
    project.mkdir()
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()
    invoke(runner, ["example", "create", "heart-disease", "."])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize")
    source_commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "record",
            "create",
            "heart-disease-prediction",
            "--title",
            "Heart disease prediction",
            "--type",
            "task",
        ],
    )
    for record_id, model in (
        ("logistic-regression", "logistic"),
        ("random-forest", "random_forest"),
    ):
        invoke(
            runner,
            [
                "record",
                "create",
                f"heart-disease-prediction/{record_id}",
                "--title",
                record_id.replace("-", " ").capitalize(),
                "--parent",
                "heart-disease-prediction",
                "--type",
                "experiment",
                "--commit",
                "HEAD",
            ],
        )
        path = f"heart-disease-prediction/{record_id}"
        assert Project.discover().vault.load(path).commit == source_commit
        invoke(
            runner,
            [
                "run",
                "--record",
                path,
                "--",
                sys.executable,
                "train.py",
                "--model",
                model,
                "--seed",
                "42",
                "--validation-fraction",
                "0.25",
            ],
        )

    config = Project.discover(project)
    config.vault.load("heart-disease-prediction")
    runs = Leaderboard.load(config).list_runs(config, sort="val/log_loss")
    assert len(runs) == 2
    records_by_id = {
        record.id: record.id.rsplit("/", 1)[-1] for record in config.vault.list()
    }
    assert {records_by_id[run.record_id] for run in runs} == set(EXPECTED_METRICS)
    for run in runs:
        record_name = records_by_id[run.record_id]
        expected_loss, expected_accuracy = EXPECTED_METRICS[record_name]
        assert float(run.metrics["val/log_loss"]) == pytest.approx(
            expected_loss, rel=1e-3
        )
        assert float(run.metrics["val/accuracy"]) == pytest.approx(
            expected_accuracy, rel=1e-3
        )
        assert run.commit == source_commit
        assert run.raw["git_state"] == "dirty"
        assert math.isfinite(float(run.metrics["val/log_loss"]))
    queried = json.loads(
        invoke(
            runner,
            ["runs", "--json"],
        ).output
    )
    assert len(queried) == 2
