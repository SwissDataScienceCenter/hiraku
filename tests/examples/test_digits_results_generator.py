from __future__ import annotations

import runpy
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

from hiraku import Project
from hiraku.jobs import Job
from hiraku.tracking import Leaderboard


@pytest.fixture
def generator():
    path = (
        Path(__file__).parents[2] / "src/hiraku/resources/examples/"
        "digits-mlflow/generate_results.py"
    )
    return runpy.run_path(str(path))


def test_digits_refuses_existing_output_and_partial_export(
    generator, tmp_path, monkeypatch
):
    with pytest.raises(FileExistsError):
        generator["generate_results"](tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("failed training")

    monkeypatch.setattr(generator["generate"].__globals__["Job"], "run", fail)
    output = tmp_path / "results"
    with pytest.raises(RuntimeError, match="failed training"):
        generator["generate_results"](output)
    assert not output.exists()


def test_bundled_digits_database_is_portable(generator, tmp_path):
    Project.create(tmp_path, example="digits-mlflow", results=True, install_skill=False)
    project = Project.discover(tmp_path)
    assert {record.id: record.title for record in project.vault.list()} == {
        "digit-classification": "Digit classification",
        "digit-classification/weak-l2": "Weak L2 regularization",
        "digit-classification/strong-l2": "Strong L2 regularization",
    }
    ids_by_name = {
        record.id.rsplit("/", 1)[-1]: record.id for record in project.vault.list()
    }
    assert project.active_record_id == ids_by_name["weak-l2"]
    generator["validate_database"](tmp_path / "mlflow.db", ids_by_name)
    assert not (tmp_path / "generate_results.py").exists()
    with sqlite3.connect(tmp_path / "mlflow.db") as db:
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
    rows = run_rows(
        leaderboard.list_runs(project),
        records=project.vault.list(),
        source=leaderboard.source.type,
    )
    assert Counter(row["record"] for row in rows) == {
        ids_by_name["weak-l2"]: 3,
        ids_by_name["strong-l2"]: 3,
    }
    assert {row["source"] for row in rows} == {"mlflow"}


def test_generate_relocate_query_and_append(generator, tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://invalid.example")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "unrelated")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
    monkeypatch.setenv("UV_NO_SYNC", "1")
    output = tmp_path / "results"
    generator["generate_results"](output)
    assert {
        p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()
    } == {
        "hiraku.yml",
        "hiraku/leaderboards/default.yml",
        "mlflow.db",
        *(
            Project.discover(output)
            .vault.file_path(record.id)
            .relative_to(output)
            .as_posix()
            for record in Project.discover(output).vault.list()
        ),
    }
    config = Project.create(
        tmp_path / "moved", example="digits-mlflow", install_skill=False
    )
    import shutil

    shutil.copytree(output, config.root, dirs_exist_ok=True)
    config = Project.discover(config.root)
    weak = config.vault.load("digit-classification/weak-l2")
    leaderboard = Leaderboard.load(config)
    runs = leaderboard.list_runs(config)
    ids_by_name = {
        record.id.rsplit("/", 1)[-1]: record.id for record in config.vault.list()
    }
    assert Counter(r.record_id for r in runs) == {
        ids_by_name["weak-l2"]: 3,
        ids_by_name["strong-l2"]: 3,
    }
    import subprocess

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
    job = Job.run(
        command,
        record_id=weak.id,
        project=config,
        env={
            "MLFLOW_TRACKING_URI": f"sqlite:///{config.root / 'mlflow.db'}",
            "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
        },
    )
    assert job.wait().exit_code == 0
    assert len(leaderboard.list_runs(config)) == 7
