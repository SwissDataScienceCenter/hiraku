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
from hiraku.project import Project

REPOSITORY = Path(__file__).parents[2]
EXAMPLE = REPOSITORY / "src" / "hiraku" / "resources" / "examples" / "digits-mlflow"
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
def test_digits_mlflow_curves_from_committed_launches(
    tmp_path, monkeypatch, mlflow_server
):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    project = tmp_path / "digits-mlflow"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_server)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    uri = mlflow_server
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_LOGGING", "false")
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()
    initialized = invoke(runner, ["example", "create", "digits-mlflow", "."])
    assert initialized.exit_code == 0
    assert not (project / "README.md").exists()
    assert not (project / "example.yml").exists()
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Digits MLflow example")
    invoke(
        runner,
        [
            "record",
            "create",
            "digit-classification",
            "--title",
            "Digit classification",
            "--type",
            "task",
            "--set",
            "goal=Compare regularization curves",
        ],
    )
    for record_id in ("weak-l2", "strong-l2"):
        invoke(
            runner,
            [
                "record",
                "create",
                f"digit-classification/{record_id}",
                "--title",
                f"{record_id.split('-')[0].capitalize()} L2 regularization",
                "--type",
                "experiment",
                "--parent",
                "digit-classification",
                "--commit",
                "HEAD",
            ],
        )
    project_config = Project.discover(project)
    ids_by_name = {
        record.id.rsplit("/", 1)[-1]: record.id
        for record in project_config.vault.list()
    }
    for record_id, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        invoke(
            runner,
            [
                "run",
                "--record",
                ids_by_name[record_id],
                "--",
                sys.executable,
                "train.py",
                "--alpha",
                str(alpha),
                "--epochs",
                "8",
                "--hidden-units",
                "64",
                "--seed",
                "42",
                "--validation-fraction",
                "0.25",
            ],
        )
    rows = json.loads(
        invoke(
            runner,
            [
                "leaderboard",
                "runs",
                "--sort",
                "val/log_loss",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 2
    assert not (project / "mlflow.db").exists()
    assert {row["record_id"] for row in rows} == {
        ids_by_name["weak-l2"],
        ids_by_name["strong-l2"],
    }
    assert [row["metrics"]["val/log_loss"] for row in rows] == sorted(
        row["metrics"]["val/log_loss"] for row in rows
    )
    client = MlflowClient(tracking_uri=uri)
    for row in rows:
        assert client.get_run(row["id"]).info.run_name == row["record_id"]
        assert row["commit"] is None
        assert row["id"]
        assert row["raw"]["status"] == "FINISHED"
        assert row["params"]["epochs"] == "8"
        assert row["params"]["seed"] == "42"
        assert float(row["params"]["alpha"]) == (
            0.0001 if row["record_id"] == ids_by_name["weak-l2"] else 1.0
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
                "leaderboard",
                "runs",
                "--record",
                "digit-classification/strong-l2",
                "--json",
            ],
        ).output
    )
    assert {row["record_id"] for row in filtered} == {ids_by_name["strong-l2"]}


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
    assert "Missing launch environment variables: HIRAKU_RECORD_ID" in result.stderr
    assert not (tmp_path / "mlflow.db").exists()


def test_digits_uses_working_directory_for_default_tracking(tmp_path):
    pytest.importorskip("mlflow")
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("HIRAKU_")
    }
    env.update(
        {
            "HIRAKU_RECORD_ID": "digits/baseline",
            "HIRAKU_JOB_ID": "a" * 32,
        }
    )
    result = subprocess.run(
        [sys.executable, str(OVERLAY / "train.py"), "--epochs", "1"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "mlflow.db").exists()
