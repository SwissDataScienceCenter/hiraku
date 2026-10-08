from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.core.config import HirakuConfig
from hiraku.core.errors import GitError, ValidationError
from hiraku.core.git import GitRepository


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[HirakuConfig, GitRepository]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("committed")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    config = HirakuConfig.init(tmp_path)
    return config, GitRepository(config)


def test_git_resolution_and_branches(tmp_path: Path) -> None:
    config, repository = _repository(tmp_path)
    assert len(repository.resolve("HEAD")) == 40
    assert repository.current_branch is not None
    branch = repository.create_record_branch("demo")
    assert branch == "hiraku/records/demo"
    assert repository.branch_exists(branch)
    with pytest.raises(ValidationError):
        repository.validate_branch("bad..branch")

    assert config.root == tmp_path


def test_custom_and_reusable_record_branches(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    current = repository.current_branch
    assert current is not None
    assert repository.create_record_branch("demo", branch=current) == current
    assert repository.create_record_branch("demo", branch="main-copy") == "main-copy"
    assert repository.branch_exists("main-copy")
    with pytest.raises(ValidationError):
        repository.create_record_branch("demo", branch="")

    default = repository.create_record_branch("baseline", start_ref="HEAD")
    assert default == "hiraku/records/baseline"
    assert repository.create_record_branch("baseline") == default

    repository.create_branch("shared-record")
    assert (
        repository.create_record_branch("second", branch="shared-record")
        == "shared-record"
    )


def test_git_errors_outside_repository(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(GitError):
        GitRepository(config)
