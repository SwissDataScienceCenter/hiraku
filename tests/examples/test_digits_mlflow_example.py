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
    initialized = invoke(runner, ["example", "create", "digits-mlflow", "."])
    assert initialized.exit_code == 0
    assert not (project / "README.md").exists()
    assert not (project / "example.yml").exists()
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Digits MLflow example")
    source_commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "record",
            "create",
            "digits",
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
                record_id,
                "--parent",
                "digits",
                "--commit",
                "HEAD",
            ],
        )
    for record_id, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        invoke(
            runner,
            [
                "run",
                "--record",
                record_id,
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
                "runs",
                "--record",
                "digits",
                "--all",
                "--sort",
                "val/log_loss",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 2
    assert (project / "mlflow.db").is_file()
    assert {row["record_id"] for row in rows} == {"weak-l2", "strong-l2"}
    assert [row["metrics"]["val/log_loss"] for row in rows] == sorted(
        row["metrics"]["val/log_loss"] for row in rows
    )
    client = MlflowClient(tracking_uri=uri)
    for row in rows:
        assert row["commit"] == source_commit
        assert row["id"]
        assert row["raw"]["status"] == "FINISHED"
        assert row["params"]["epochs"] == "8"
        assert row["params"]["seed"] == "42"
        assert float(row["params"]["alpha"]) == (
            0.0001 if row["record_id"] == "weak-l2" else 1.0
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
                "runs",
                "--record",
                "strong-l2",
                "--json",
            ],
        ).output
    )
    assert {row["record_id"] for row in filtered} == {"strong-l2"}


def test_digits_runs_without_hiraku_provenance(tmp_path):
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
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "mlflow.db").exists()


def test_digits_uses_job_id_from_environment_without_persisted_metadata(tmp_path):
    pytest.importorskip("mlflow")
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("HIRAKU_")
    }
    env.update(
        {
            "HIRAKU_JOB_ID": "missing-job-id",
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
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")
    experiment = client.get_experiment_by_name("hiraku-digits")
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    assert runs[0].data.tags["hiraku.job_id"] == "missing-job-id"
