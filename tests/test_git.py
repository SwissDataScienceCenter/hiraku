from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import GitError
from hiraku.git import GitRepository, init_code_repository
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


def _commit(path: Path) -> str:
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "Initial metadata")
    return _git(path, "rev-parse", "HEAD")


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


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source"
    path.mkdir()
    _git(path, "init", "-q")
    (path / "README.md").write_text("Research history\n")
    _commit(path)
    return path


@pytest.mark.parametrize(
    ("command", "project_name", "example_file"),
    [
        (["init", "minimal"], "minimal", None),
        (["example", "create", "digits"], "digits", "train.py"),
    ],
)
def test_project_creation_does_not_invoke_git(
    tmp_path: Path,
    monkeypatch,
    command: list[str],
    project_name: str,
    example_file: str | None,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Git must not be invoked during plain initialization")

    monkeypatch.setattr("hiraku.git.GitRepository._run", forbidden)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, command)
    assert result.exit_code == 0, result.output
    project = tmp_path / project_name
    assert (project / "hiraku.yml").is_file()
    if example_file is not None:
        assert (project / example_file).is_file()
    assert not (project / ".git").exists()


def test_init_without_git_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(main, ["init"]).exit_code == 0
    failed = CliRunner().invoke(main, ["init", "other", "--git"])
    assert failed.exit_code == 1
    assert "not found" in failed.output
    assert not (tmp_path / "other").exists()


def test_init_git_creates_code_repo_and_can_be_repeated(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
    root = tmp_path / "project"
    _git(root, "config", "hiraku.test", "preserved")
    assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
    assert _git(root, "config", "hiraku.test") == "preserved"
    assert not _git(root, "ls-files")


def test_git_init_preserves_linked_code_worktree(tmp_path: Path, source: Path) -> None:
    checkout = tmp_path / "worktree"
    _git(source, "worktree", "add", "-b", "research", str(checkout))
    config = Project.create(checkout)
    before = (checkout / ".git").read_bytes()
    init_code_repository(config)
    assert (checkout / ".git").read_bytes() == before
    assert _git(checkout, "rev-parse", "HEAD") == _git(source, "rev-parse", "HEAD")
