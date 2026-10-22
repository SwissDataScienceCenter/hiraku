from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.dashboard.data import file_rows, load, run_rows


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]) -> str:
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.integration
def test_digits_snakemake_example_tracks_both_record_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("snakemake")
    pytest.importorskip("mlflow")
    if shutil.which("snakemake") is None or shutil.which("hiraku") is None:
        pytest.skip("snakemake and hiraku executables required")
    project = tmp_path / "digits-snakemake"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_LOGGING", "false")
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    invoke(runner, ["init", "--example", "digits-snakemake"])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Digits Snakemake example")

    invoke(
        runner,
        [
            "record",
            "create",
            "digits",
            "--type",
            "task",
            "--set",
            "goal=Compare regularization in a workflow",
            "--set",
            "tracking_backend={type: csv, path: hiraku/runs.csv}",
            "--set",
            "entrypoints={prepare: {command: [python, scripts/prepare_data.py]}}",
        ],
    )
    for record_id, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        invoke(
            runner,
            [
                "record",
                "create",
                record_id,
                "--parent",
                "digits",
                "--type",
                "experiment",
                "--set",
                "tracking_backend={type: mlflow, database: mlflow.db, "
                "experiment_name: hiraku-digits}",
                "--set",
                "entrypoints={train: {command: [python, scripts/train.py], args: "
                f"{{alpha: {alpha}, epochs: 40, hidden-units: 64, seed: 42}}}}}}",
            ],
        )
    git(project, "add", ".")
    git(project, "commit", "-m", "add Digits records")
    head = git(project, "rev-parse", "HEAD")
    run = ["snakemake", "run", "--record", "digits", "--config", "train=--epochs 2"]

    invoke(runner, run)

    config = HirakuConfig.discover(project)
    [workflow] = json.loads(invoke(runner, ["runs", "--record", "digits", "--json"]))
    assert workflow["entrypoint"] == "snakemake"
    assert workflow["commit"] == head
    assert workflow["raw"]["outcome"] == "succeeded"
    assert workflow["raw"]["git_state"] == "clean"
    # Each job names the record and entrypoint that ran it and its files.
    jobs = {job["rule"]: job for job in json.loads(workflow["raw"]["jobs"])}
    assert {
        rule: (job["record"], job["entrypoint"], job["outputs"], job["ran"])
        for rule, job in jobs.items()
    } == {
        "digits__prepare": ("digits", "prepare", ["data/digits.npz"], True),
        "weak_l2__train": ("weak-l2", "train", ["results/weak-l2.json"], True),
        "strong_l2__train": ("strong-l2", "train", ["results/strong-l2.json"], True),
    }
    assert jobs["weak_l2__train"]["command"].endswith("--epochs 2")

    client_uri = f"sqlite:///{project / 'mlflow.db'}"
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=client_uri)
    for record_id, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        rows = json.loads(invoke(runner, ["runs", "--record", record_id, "--json"]))
        assert len(rows) == 1
        training = rows[0]
        assert training["record_id"] == record_id
        assert training["entrypoint"] == "train"
        assert training["commit"] == head
        assert float(training["params"]["alpha"]) == alpha
        assert training["params"]["epochs"] == "2"
        assert training["params"]["hidden_units"] == "64"
        assert training["params"]["seed"] == "42"
        # The workflow run is the explicit link between both record roles.
        assert training["raw"]["tags"]["hiraku.workflow_run_id"] == workflow["id"]
        for metric in ("train/log_loss", "val/log_loss", "train/accuracy"):
            history = sorted(
                client.get_metric_history(training["id"], metric),
                key=lambda point: point.step,
            )
            assert [point.step for point in history] == [1, 2]
            assert all(math.isfinite(point.value) for point in history)
            assert training["metrics"][metric] == pytest.approx(history[-1].value)

        result = json.loads((project / "results" / f"{record_id}.json").read_text())
        assert result["record_id"] == record_id
        assert result["workflow_run_id"] == workflow["id"]
        assert result["mlflow_run_id"] == training["id"]
        assert result["alpha"] == alpha
        assert len(result["history"]["val/log_loss"]) == 2

    # The dashboard sees the workflow and both experiment records together.
    dashboard = run_rows(load(config))
    assert {row["record"] for row in dashboard} == {"digits", "weak-l2", "strong-l2"}
    assert {row["source"] for row in dashboard} == {"csv", "mlflow"}
    files = {row["path"]: row for row in file_rows(load(config))}
    assert files["data/digits.npz"]["produced_by"] == "digits · prepare"
    assert files["data/digits.npz"]["used_by"] == [
        "weak-l2 · train",
        "strong-l2 · train",
    ]

    # The evidence stays with the records, outside the code files.
    assert (project / "hiraku" / "runs.csv").exists()
    assert not (project / "runs.csv").exists()
    assert "no-op" in invoke(runner, run)
