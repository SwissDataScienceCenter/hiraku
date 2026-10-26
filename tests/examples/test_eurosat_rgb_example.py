from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


def test_eurosat_rgb_example_initializes_without_downloading_data(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "eurosat-rgb"
    project.mkdir()
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    runner = CliRunner()

    invoke(runner, ["example", "create", "eurosat-rgb", "."])

    assert (project / "train.py").is_file()
    assert (project / "pyproject.toml").is_file()
    assert "/data/" in (project / ".gitignore").read_text()
    assert not (project / "data").exists()
