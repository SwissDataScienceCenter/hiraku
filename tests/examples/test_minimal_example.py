from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


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

    initialized = invoke(runner, ["init", "--example", "minimal"])
    assert initialized.exit_code == 0
    assert not (project / "README.md").exists()
    assert not (project / "example.yml").exists()
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize minimal example")
    commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "record",
            "create",
            "my-task",
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
            "--commit",
            "HEAD",
            "--set",
            f"entrypoints={{evaluate: {{command: [{sys.executable}, evaluate.py]}}}}",
        ],
    )

    invoke(runner, ["launch", "evaluate", "--record", "baseline"])

    config = HirakuConfig.discover(project)
    runs = load_record("baseline", config=config).runs(config=config)
    assert len(runs) == 1
    assert runs[0].commit == commit
    assert runs[0].raw["git_state"] == "clean"
    assert runs[0].timestamp is not None
    assert runs[0].duration is not None and runs[0].duration >= 0
    assert (project / "runs.csv").is_file()
    assert git(project, "status", "--porcelain") == ""
