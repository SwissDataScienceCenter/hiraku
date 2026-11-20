from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from hiraku.cli import main
from hiraku.project import Project


@pytest.fixture
def cli() -> Callable[..., Result]:
    def run(*args: str) -> Result:
        return CliRunner().invoke(main, args, catch_exceptions=False)

    return run


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    project = Project.create(tmp_path)
    monkeypatch.chdir(tmp_path)
    return project


@pytest.fixture
def git() -> Callable[..., str]:
    def run(path: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=path, text=True, capture_output=True, check=True
        ).stdout.strip()

    return run


@pytest.fixture(autouse=True)
def disable_git_commit_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable commit signing in Git subprocesses started by tests."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
