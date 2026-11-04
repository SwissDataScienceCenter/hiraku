from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.errors import GitError
from hiraku.git import GitRepository
from hiraku.project import Project


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Project, GitRepository]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("committed")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    config = Project.create(tmp_path)
    return config, GitRepository(config.root)


def test_git_resolution_and_current_branch(tmp_path: Path) -> None:
    config, repository = _repository(tmp_path)
    assert len(repository.resolve("HEAD")) == 40
    assert repository.current_branch is not None
    sha = repository.resolve("HEAD")
    _git(tmp_path, "switch", "-c", "research")
    assert repository.current_branch == "research"
    assert repository.resolve("research") == sha
    assert config.root == tmp_path


def test_detached_head_has_no_current_branch(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    sha = repository.resolve("HEAD")
    _git(tmp_path, "checkout", "--detach", sha)
    assert repository.current_branch is None
    assert repository.resolve("HEAD") == sha


@pytest.mark.parametrize("ref", ["", "missing", "--invalid"])
def test_invalid_commit_reference(tmp_path: Path, ref: str) -> None:
    _, repository = _repository(tmp_path)
    with pytest.raises(GitError):
        repository.resolve(ref)


def test_git_errors_outside_repository(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    with pytest.raises(GitError):
        GitRepository(config.root)
