from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record

EXPECTED_METRICS = {
    "logistic-baseline": (0.32254890, 0.85526316),
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
    git(project, "add", "-f", "hiraku/dispatch")
    git(project, "commit", "-m", "initialize")
    source_commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "record",
            "create",
            "heart-disease",
            "--type",
            "task",
            "--set",
            "metrics=[{name: val/log_loss, direction: minimize}, "
            "{name: val/accuracy, direction: maximize}]",
        ],
    )
    for record_id, model in (
        ("logistic-baseline", "logistic"),
        ("random-forest", "random_forest"),
    ):
        command = json.dumps(
            [
                sys.executable,
                "train.py",
                "--model",
                model,
                "--seed",
                "42",
                "--validation-fraction",
                "0.25",
            ]
        )
        invoke(
            runner,
            [
                "record",
                "create",
                record_id,
                "--parent",
                "heart-disease",
                "--type",
                "experiment",
                "--commit",
                "HEAD",
                "--set",
                f"command={command}",
            ],
        )
        assert load_record(record_id).commit == source_commit
        job_id = invoke(
            runner, ["run", "--backend", "local", "--record", record_id]
        ).output.strip()
        invoke(runner, ["job", "wait", job_id])

    config = HirakuConfig.discover(project)
    root = load_record("heart-disease", config=config)
    runs = root.resolve_tracking_backend(config=config).list_runs(sort="val/log_loss")
    assert len(runs) == 2
    assert {run.record_id for run in runs} == set(EXPECTED_METRICS)
    for run in runs:
        expected_loss, expected_accuracy = EXPECTED_METRICS[run.record_id]
        assert float(run.metrics["val/log_loss"]) == pytest.approx(
            expected_loss, rel=1e-3
        )
        assert float(run.metrics["val/accuracy"]) == pytest.approx(
            expected_accuracy, rel=1e-3
        )
        assert run.commit == source_commit
        assert run.raw["git_state"] == "clean"
        assert math.isfinite(float(run.metrics["val/log_loss"]))
    queried = json.loads(
        invoke(
            runner,
            ["runs", "--record", "heart-disease", "--all", "--json"],
        ).output
    )
    assert len(queried) == 2
