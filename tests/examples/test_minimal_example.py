from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import Leaderboard


@pytest.mark.parametrize(
    "existing", ["", "commit,git_state,record_id\nold,clean,test\n"]
)
def test_minimal_csv_header_validation(tmp_path, existing):
    script = (
        Path(__file__).parents[2]
        / "src/hiraku/resources/examples/minimal/src/evaluate.py"
    )
    path = tmp_path / "runs.csv"
    path.write_text(existing)
    environment = {**os.environ, "HIRAKU_RECORD_ID": "test", "HIRAKU_JOB_ID": "job"}
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    if existing:
        assert result.returncode != 0
        assert "Unexpected runs CSV header" in result.stderr
        assert path.read_bytes() == existing.encode()
    else:
        assert result.returncode == 0, result.stderr
        before = path.read_text()
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert path.read_text().startswith(before)
        assert len(path.read_text().splitlines()) == 3


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


def test_minimal_example_writes_clean_project_root_run(tmp_path, monkeypatch) -> None:
    project = tmp_path / "minimal"
    project.mkdir()
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    initialized = invoke(runner, ["example", "create", "minimal", "."])
    assert initialized.exit_code == 0
    assert not (project / "README.md").exists()
    assert not (project / "example.yml").exists()
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize minimal example")
    invoke(
        runner,
        [
            "record",
            "create",
            "binary-classification",
            "--title",
            "Binary classification",
            "--type",
            "task",
        ],
    )
    invoke(
        runner,
        [
            "record",
            "create",
            "binary-classification/threshold-baseline",
            "--title",
            "Threshold baseline",
            "--type",
            "experiment",
            "--parent",
            "binary-classification",
            "--commit",
            "HEAD",
        ],
    )
    git(project, "add", "hiraku", "hiraku.yml")
    git(project, "commit", "-m", "add example records")

    invoke(
        runner,
        [
            "run",
            "--record",
            "binary-classification/threshold-baseline",
            "--",
            sys.executable,
            "evaluate.py",
        ],
    )

    config = Project.discover(project)
    record = config.vault.load("binary-classification/threshold-baseline")
    runs = Leaderboard.load(config).list_runs(config, record_id=record.id)
    assert len(runs) == 1
    assert runs[0].commit is None
    assert runs[0].timestamp is not None
    assert runs[0].duration is not None and runs[0].duration >= 0
    assert (project / "runs.csv").is_file()
    assert git(project, "status", "--porcelain") == ""


def test_minimal_documented_sequence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    invoke(runner, ["example", "create", "minimal", "my-project", "--git"])
    project = tmp_path / "my-project"
    monkeypatch.chdir(project)
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    git(project, "add", ".")
    git(project, "commit", "-m", "initial commit")
    invoke(runner, ["status"])
    invoke(
        runner,
        [
            "record",
            "create",
            "binary-classification",
            "--title",
            "Binary classification",
            "--type",
            "task",
            "--set",
            "goal=Improve validation accuracy on my dataset",
        ],
    )
    invoke(
        runner,
        [
            "record",
            "create",
            "binary-classification/threshold-baseline",
            "--title",
            "Threshold baseline",
            "--parent",
            "binary-classification",
            "--type",
            "experiment",
            "--set",
            "hypothesis=The existing representation establishes a reference.",
            "--commit",
            "HEAD",
        ],
    )
    for seed in (0, 1):
        invoke(
            runner,
            [
                "run",
                "--record",
                "binary-classification/threshold-baseline",
                "--",
                sys.executable,
                "evaluate.py",
                "--seed",
                str(seed),
            ],
        )
    assert "baseline" in invoke(runner, ["record", "list"]).output
    runs = json.loads(
        invoke(
            runner,
            [
                "leaderboard",
                "runs",
                "--record",
                "binary-classification/threshold-baseline",
                "--json",
            ],
        ).output
    )
    assert len(runs) == 2
    assert {str(run["params"]["seed"]) for run in runs} == {"0", "1"}
    assert all(run["commit"] is None for run in runs)
