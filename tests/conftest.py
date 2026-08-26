from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def run(
    cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    if env:
        merged.update(env)
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=merged,
    )
    if check and result.returncode:
        raise AssertionError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def hiraku(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(cwd, sys.executable, "-m", "hiraku", *args, check=check)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    run(tmp_path, "git", "init", "-b", "main")
    run(tmp_path, "git", "config", "user.email", "tests@example.com")
    run(tmp_path, "git", "config", "user.name", "Hiraku Tests")
    hiraku(tmp_path, "init")
    run(tmp_path, "git", "add", ".")
    run(tmp_path, "git", "commit", "-m", "initialize hiraku")
    return tmp_path
