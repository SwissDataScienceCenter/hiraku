from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "autoresearch"


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


def test_autoresearch_cpu_mock_tracks_local_launch(tmp_path, monkeypatch, capfd):
    assert "mlflow" not in (EXAMPLE / "pyproject.toml").read_text().lower()
    assert "mlflow" not in (EXAMPLE / "tracking_writer.py").read_text().lower()
    project = tmp_path / "autoresearch"
    shutil.copytree(EXAMPLE, project)
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()
    invoke(runner, ["init"])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize autoresearch example")
    commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "task",
            "create",
            "autoresearch",
            "--goal",
            "Minimize val_bpb",
            "--set",
            "metrics=[{name: val_bpb, direction: minimize}]",
            "--set",
            f"entrypoints={{cpu-mock: {{command: [{sys.executable}, cpu_mock.py], "
            "args: {val-bpb: 1.0}}}",
        ],
    )
    assert git(project, "status", "--porcelain") == ""
    invoke(
        runner,
        [
            "experiment",
            "create",
            "contract",
            "--commit",
            "HEAD",
        ],
    )
    invoke(
        runner,
        [
            "launch",
            "contract:cpu-mock",
            "--",
            "--val-bpb",
            "0.9",
        ],
    )
    assert "val_bpb: 0.900000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--task",
                "autoresearch",
                "--experiment",
                "contract",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "autoresearch"
    assert row["experiment_id"] == "contract"
    assert row["commit"] == commit
    assert row["entrypoint"] == "cpu-mock"
    assert row["raw"]["git_state"] == "clean"
    assert float(row["metrics"]["val_bpb"]) == 0.9
    assert row["raw"]["status"] == "finished"
    assert git(project, "status", "--porcelain") == ""


def test_autoresearch_mock_requires_launch_provenance(tmp_path):
    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "cpu_mock.py")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "Missing launch environment variables: HIRAKU_TASK_ID" in result.stderr
    assert not (tmp_path / "runs.csv").exists()
