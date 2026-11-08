from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import TrackingBackendFactory


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
            "my-task",
            "--type",
            "task",
            "--set",
            "metrics=[{name: val/accuracy, direction: maximize}]",
        ],
    )
    invoke(
        runner,
        [
            "record",
            "create",
            "baseline",
            "--type",
            "experiment",
            "--parent",
            "my-task",
            "--commit",
            "HEAD",
        ],
    )
    git(project, "add", "hiraku", "hiraku.yml")
    git(project, "commit", "-m", "add example records")
    commit = git(project, "rev-parse", "HEAD")

    invoke(
        runner,
        ["run", "--record", "baseline", "--", sys.executable, "evaluate.py"],
    )

    config = Project.discover(project)
    record = config.vault.get("baseline")
    runs = (
        TrackingBackendFactory(config)
        .create(config.tracking_backend)
        .list_runs(record_id=record.id)
    )
    assert len(runs) == 1
    assert runs[0].commit == commit
    assert runs[0].raw["git_state"] == "clean"
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
    commit = git(project, "rev-parse", "HEAD")
    invoke(runner, ["status"])
    invoke(
        runner,
        [
            "record",
            "create",
            "my-task",
            "--type",
            "task",
            "--set",
            "goal=Improve validation accuracy on my dataset",
            "--set",
            "metrics=[{name: val/accuracy, direction: maximize}]",
        ],
    )
    invoke(
        runner,
        [
            "record",
            "create",
            "baseline",
            "--parent",
            "my-task",
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
                "baseline",
                "--",
                sys.executable,
                "evaluate.py",
                "--seed",
                str(seed),
            ],
        )
    assert "baseline" in invoke(runner, ["record", "list"]).output
    runs = json.loads(invoke(runner, ["runs", "--record", "baseline", "--json"]).output)
    assert len(runs) == 2
    assert {str(run["params"]["seed"]) for run in runs} == {"0", "1"}
    assert {run["commit"] for run in runs} == {commit}
