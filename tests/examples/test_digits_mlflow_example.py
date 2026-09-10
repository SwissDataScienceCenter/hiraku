from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main

REPOSITORY = Path(__file__).parents[2]
EXAMPLE = (
    REPOSITORY / "src" / "hiraku" / "resources" / "examples" / "digits-mlflow"
)
OVERLAY = EXAMPLE / "src"


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_digits_mlflow_curves_from_committed_launches(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    project = tmp_path / "digits-mlflow"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    uri = f"sqlite:///{project / 'mlflow.db'}"
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_LOGGING", "false")
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()
    initialized = invoke(runner, ["init", "--example", "digits-mlflow"])
    assert "Created file: train.py" in initialized.output
    assert "Created file: pyproject.toml" in initialized.output
    assert "Created file: hiraku.yml" in initialized.output
    assert not (project / "README.md").exists()
    assert not (project / "example.yml").exists()
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Digits MLflow example")
    source_commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "task",
            "create",
            "digits",
            "--goal",
            "Compare regularization curves",
            "--set",
            "tracking_backend={type: mlflow, database: mlflow.db, "
            "experiment_name: hiraku-digits}",
        ],
    )
    for experiment, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        invoke(
            runner,
            [
                "experiment",
                "create",
                experiment,
                "--commit",
                "HEAD",
                "--set",
                f"entrypoints={{train: {{command: [{sys.executable}, train.py], args: "
                f"{{alpha: {alpha}, epochs: 8, hidden-units: 64, seed: 42, "
                "validation-fraction: 0.25}}}",
            ],
        )
    for experiment in ("weak-l2", "strong-l2"):
        invoke(runner, ["launch", f"{experiment}:train"])
    rows = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--task",
                "digits",
                "--sort",
                "val/log_loss",
                "--ascending",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 2
    assert (project / "mlflow.db").is_file()
    assert {row["experiment_id"] for row in rows} == {"weak-l2", "strong-l2"}
    assert [row["metrics"]["val/log_loss"] for row in rows] == sorted(
        row["metrics"]["val/log_loss"] for row in rows
    )
    client = MlflowClient(tracking_uri=uri)
    for row in rows:
        assert row["task_id"] == "digits"
        assert row["commit"] == source_commit
        assert row["entrypoint"] == "train"
        assert row["raw"]["status"] == "FINISHED"
        assert row["params"]["epochs"] == "8"
        assert row["params"]["seed"] == "42"
        assert float(row["params"]["alpha"]) == (
            0.0001 if row["experiment_id"] == "weak-l2" else 1.0
        )
        for metric in (
            "train/log_loss",
            "val/log_loss",
            "train/accuracy",
            "val/accuracy",
        ):
            history = sorted(
                client.get_metric_history(row["id"], metric), key=lambda m: m.step
            )
            assert [point.step for point in history] == list(range(1, 9))
            assert all(math.isfinite(point.value) for point in history)
            assert row["metrics"][metric] == pytest.approx(history[-1].value)
            if metric.endswith("accuracy"):
                assert all(0 <= point.value <= 1 for point in history)
            else:
                assert all(point.value >= 0 for point in history)
                assert history[-1].value < history[0].value
        assert row["metrics"]["runtime/seconds"] > 0
    filtered = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--task",
                "digits",
                "--experiment",
                "strong-l2",
                "--json",
            ],
        ).output
    )
    assert len(filtered) == 1
    assert filtered[0]["experiment_id"] == "strong-l2"


def test_digits_requires_provenance_before_creating_runs(tmp_path):
    pytest.importorskip("mlflow")
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("HIRAKU_")
    }
    env["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path / 'mlflow.db'}"
    env["MLFLOW_EXPERIMENT_NAME"] = "hiraku-digits"
    result = subprocess.run(
        [sys.executable, str(OVERLAY / "train.py"), "--epochs", "1"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "Missing launch environment variables: HIRAKU_TASK_ID" in result.stderr
    assert not (tmp_path / "mlflow.db").exists()
