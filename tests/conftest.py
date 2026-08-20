from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


def run_process(
    command: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_process(["git", *args], repo, check=check)


def commit_all(repo: Path, message: str = "checkpoint") -> None:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Hiraku Tests")
    git(path, "config", "user.email", "hiraku-tests@example.invalid")
    (path / "README.md").write_text("# Test project\n", encoding="utf-8")
    commit_all(path, "initial")
    yield path


@pytest.fixture
def hiraku_command() -> list[str]:
    return [sys.executable, "-m", "hiraku"]
