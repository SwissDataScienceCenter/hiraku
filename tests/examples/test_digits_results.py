from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections import Counter
from math import isfinite
from pathlib import Path

from hiraku import Project
from hiraku.examples import Example
from hiraku.job import prepare_job, run_local
from hiraku.tracking import Leaderboard


def test_digits_database_is_portable(tmp_path, monkeypatch, mlflow_server_factory):
    Example.create("digits", tmp_path, with_results=True)
    project = Project.discover(tmp_path)
    assert {record.id: record.title for record in project.vault.list()} == {
        "digit-classification": "Digit classification",
        "digit-classification/weak-l2": "Weak L2 regularization",
        "digit-classification/strong-l2": "Strong L2 regularization",
    }
    ids_by_name = {
        record.id.rsplit("/", 1)[-1]: record.id for record in project.vault.list()
    }
    tracking_uri = mlflow_server_factory(tmp_path / "mlflow.db")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
    with sqlite3.connect(tmp_path / "mlflow.db") as db:
        assert db.execute(
            "SELECT status, COUNT(*) FROM runs GROUP BY status"
        ).fetchall() == [("FINISHED", 6)]
        for (uri,) in db.execute("SELECT artifact_uri FROM runs"):
            assert uri.startswith("mlruns/") and not Path(uri).is_absolute()
        for (uri,) in db.execute("SELECT artifact_location FROM experiments"):
            assert uri.startswith("mlruns/") and not Path(uri).is_absolute()
        assert (
            db.execute(
                "SELECT COUNT(*) FROM metrics WHERE key='val/log_loss'"
            ).fetchone()[0]
            == 120
        )

    from hiraku.dashboard.data import run_rows

    leaderboard = Leaderboard.load(project)
    runs = leaderboard.list_runs(project)
    for name, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        experiment_runs = [run for run in runs if run.record_id == ids_by_name[name]]
        assert sorted(run.params["seed"] for run in experiment_runs) == ["1", "2", "3"]
        for run in experiment_runs:
            assert float(run.params["alpha"]) == alpha
            assert run.params["epochs"] == "20"
            assert isfinite(run.metrics["val/log_loss"])
            assert isfinite(run.metrics["val/accuracy"])

    rows = run_rows(
        runs,
        records=project.vault.list(),
        source=leaderboard.source.type,
    )
    assert Counter(row["record"] for row in rows) == {
        ids_by_name["weak-l2"]: 3,
        ids_by_name["strong-l2"]: 3,
    }
    assert {row["source"] for row in rows} == {"mlflow"}


def test_append_run_to_prepared_digits_results(
    tmp_path, monkeypatch, mlflow_server_factory
):
    config = Project.load(
        Example.create("digits", tmp_path / "digits", with_results=True)
    )
    tracking_uri = mlflow_server_factory(config.root / "mlflow.db")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
    weak = config.vault.load("digit-classification/weak-l2")
    leaderboard = Leaderboard.load(config)
    for arguments in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "relocated source",
        ],
    ):
        subprocess.run(["git", *arguments], cwd=config.root, check=True)
    command = [
        sys.executable,
        "train.py",
        "--alpha",
        "0.0001",
        "--epochs",
        "8",
        "--hidden-units",
        "64",
        "--seed",
        "4",
        "--validation-fraction",
        "0.25",
    ]
    job = prepare_job(
        command,
        record_id=weak.id,
        project=config,
        directory=config.root,
        env={
            "MLFLOW_TRACKING_URI": tracking_uri,
            "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
        },
    )
    assert run_local(job, project=config).exit_code == 0
    assert len(leaderboard.list_runs(config)) == 7
