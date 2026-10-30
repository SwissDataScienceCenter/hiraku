from __future__ import annotations

import runpy
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

from hiraku import HirakuConfig
from hiraku.execution import execute_command
from hiraku.record import load_record


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

    monkeypatch.setitem(generator["generate"].__globals__, "execute_command", fail)
    output = tmp_path / "results"
    with pytest.raises(RuntimeError, match="failed training"):
        generator["generate_results"](output)
    assert not output.exists()


def test_bundled_digits_database_is_portable(generator, tmp_path):
    HirakuConfig.init(
        tmp_path, example="digits-mlflow", results=True, install_skill=False
    )
    generator["validate_database"](tmp_path / "mlflow.db")
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

    from hiraku.dashboard.data import load

    dashboard = load(HirakuConfig.discover(tmp_path))
    assert dashboard.errors == []
    assert Counter(run.record_id for run in dashboard.runs) == {
        "weak-l2": 3,
        "strong-l2": 3,
    }
    assert set(dashboard.sources.values()) == {"mlflow"}


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
        "mlflow.db",
        *(
            f"hiraku/records/digits/{name}.md"
            for name in ("digits", "weak-l2", "strong-l2")
        ),
    }
    config = HirakuConfig.init(
        tmp_path / "moved", example="digits-mlflow", install_skill=False
    )
    import shutil

    shutil.copytree(output, config.root, dirs_exist_ok=True)
    config = HirakuConfig.discover(config.root)
    weak = load_record("weak-l2", config=config)
    backend = weak.resolve_tracking_backend(config=config)
    runs = backend.list_runs()
    assert Counter(r.record_id for r in runs) == {"weak-l2": 3, "strong-l2": 3}
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
    result = execute_command(
        command,
        record_id=weak.id,
        config=config,
        env={
            "MLFLOW_TRACKING_URI": f"sqlite:///{config.root / 'mlflow.db'}",
            "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
        },
    )
    assert result.returncode == 0
    assert len(backend.list_runs()) == 7
