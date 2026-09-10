from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.data import load_experiment, load_task
from hiraku.tracking import CSVBackend

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "heart-disease"
DATA_SHA256 = "a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8"
EXPECTED_METRICS = {
    "logistic-baseline": (0.32254890, 0.85526316),
    "random-forest": (0.35226763, 0.86842105),
}


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_documented_heart_disease_workflow(tmp_path: Path, monkeypatch) -> None:
    source_files = {
        path.relative_to(EXAMPLE): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in EXAMPLE.rglob("*")
        if path.is_file()
    }
    project = tmp_path / "heart-disease"
    shutil.copytree(EXAMPLE, project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    monkeypatch.chdir(project)
    runner = CliRunner()

    invoke(runner, ["init"])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Heart Disease example")
    shared_commit = git(project, "rev-parse", "HEAD")
    assert len(shared_commit) == 40
    assert (
        hashlib.sha256(
            (project / "data" / "processed.cleveland.data").read_bytes()
        ).hexdigest()
        == DATA_SHA256
    )

    invoke(
        runner,
        [
            "task",
            "create",
            "heart-disease",
            "--goal",
            "Compare two model families on a fixed Cleveland cohort",
            "--question",
            "Does a nonlinear random forest improve on a calibrated linear baseline?",
            "--set",
            "metrics=[{name: val/log_loss, direction: minimize}, "
            "{name: val/accuracy, direction: maximize}]",
        ],
    )
    config = HirakuConfig.discover(project)
    task = load_task("heart-disease", config=config)
    assert config.active_task_id == "heart-disease"
    assert task.entrypoints == {}

    experiments = {
        "logistic-baseline": {
            "summary": "Logistic regression baseline",
            "hypothesis": (
                "Regularized logistic regression provides a calibrated baseline"
            ),
            "model": "logistic",
        },
        "random-forest": {
            "summary": "Random forest comparison",
            "hypothesis": "Nonlinear feature interactions improve validation log loss",
            "model": "random_forest",
        },
    }
    for experiment_id, expected in experiments.items():
        invoke(
            runner,
            [
                "experiment",
                "create",
                experiment_id,
                "--summary",
                expected["summary"],
                "--hypothesis",
                expected["hypothesis"],
                "--commit",
                "HEAD",
                "--set",
                f"entrypoints={{train: {{command: [{sys.executable}, train.py], args: "
                f"{{model: {expected['model']}, seed: 42, "
                "validation-fraction: 0.25}}}",
            ],
        )
        experiment = load_experiment(experiment_id, task=task, config=config)
        assert experiment.commit == shared_commit
        assert {
            name: entrypoint.model_dump()
            for name, entrypoint in experiment.entrypoints.items()
        } == {
            "train": {
                "command": [sys.executable, "train.py"],
                "args": {
                    "model": expected["model"],
                    "seed": 42,
                    "validation-fraction": 0.25,
                },
            }
        }

    assert git(project, "status", "--porcelain") == ""
    invoke(runner, ["launch", "logistic-baseline:train"])
    invoke(runner, ["launch", "random-forest:train"])
    assert git(project, "status", "--porcelain") == ""

    all_runs = json.loads(invoke(runner, ["run", "list", "--json"]).output)
    selected = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--params",
                "model,seed,validation_fraction",
                "--metrics",
                "val/log_loss,val/accuracy",
                "--json",
            ],
        ).output
    )
    filtered = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--experiment",
                "logistic-baseline",
                "--params",
                "model,seed,validation_fraction",
                "--metrics",
                "val/log_loss,val/accuracy",
                "--json",
            ],
        ).output
    )
    sorted_runs = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--params",
                "model,seed,validation_fraction",
                "--metrics",
                "val/log_loss,val/accuracy",
                "--sort",
                "val/log_loss",
                "--ascending",
                "--json",
            ],
        ).output
    )
    assert selected == all_runs
    assert len(filtered) == 1
    assert filtered[0]["experiment_id"] == "logistic-baseline"
    assert len(sorted_runs) == 2

    csv_path = project / "runs.csv"
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    normalized = CSVBackend(config).list_runs(task_id="heart-disease")
    assert len(rows) == len(normalized) == 2
    for run in normalized:
        assert run.task_id == "heart-disease"
        assert run.experiment_id in experiments
        assert run.entrypoint == "train"
        assert run.commit == shared_commit
        assert run.raw["git_state"] == "clean"
        assert run.params == {
            "model": experiments[run.experiment_id]["model"],
            "seed": "42",
            "validation_fraction": "0.25",
        }
        loss = float(run.metrics["val/log_loss"])
        accuracy = float(run.metrics["val/accuracy"])
        assert math.isfinite(loss)
        assert math.isfinite(accuracy)
        assert 0.0 <= accuracy <= 1.0
        expected_loss, expected_accuracy = EXPECTED_METRICS[run.experiment_id]
        assert loss == pytest.approx(expected_loss, rel=1e-3, abs=1e-4)
        assert accuracy == pytest.approx(expected_accuracy, rel=1e-3, abs=1e-4)

    assert [float(run["metrics"]["val/log_loss"]) for run in sorted_runs] == sorted(
        float(run["metrics"]["val/log_loss"]) for run in sorted_runs
    )
    assert [run["experiment_id"] for run in sorted_runs] == [
        "logistic-baseline",
        "random-forest",
    ]
    assert source_files == {
        path.relative_to(EXAMPLE): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in EXAMPLE.rglob("*")
        if path.is_file()
    }
