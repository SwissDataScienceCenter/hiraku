from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.data import load_task

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "minimal"


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
    shutil.copytree(EXAMPLE, project)
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    invoke(runner, ["init"])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize minimal example")
    commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        [
            "task",
            "create",
            "my-task",
            "--set",
            "metrics=[{name: val/accuracy, direction: maximize}]",
        ],
    )
    invoke(
        runner,
        [
            "experiment",
            "create",
            "baseline",
            "--commit",
            "HEAD",
            "--set",
            f"entrypoints={{evaluate: {{command: [{sys.executable}, evaluate.py]}}}}",
        ],
    )

    invoke(runner, ["launch", "baseline:evaluate"])

    config = HirakuConfig.discover(project)
    runs = load_task("my-task", config=config).runs(config=config)
    assert len(runs) == 1
    assert runs[0].commit == commit
    assert runs[0].raw["git_state"] == "clean"
    assert (project / "runs.csv").is_file()
    assert git(project, "status", "--porcelain") == ""
