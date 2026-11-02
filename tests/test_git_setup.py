from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.git import init_code_repository


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(path: Path) -> str:
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "add", ".")
    git(path, "commit", "-qm", "Initial metadata")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    git(path, "init", "-q")
    (path / "README.md").write_text("Research history\n")
    commit(path)
    return path


@pytest.mark.parametrize(
    "command",
    [
        ["init", "minimal", "--records-path", "knowledge"],
        ["example", "create", "minimal"],
    ],
)
def test_project_creation_does_not_invoke_git(tmp_path, monkeypatch, command):
    def forbidden(*args, **kwargs):
        pytest.fail("Git must not be invoked during plain initialization")

    monkeypatch.setattr("hiraku.git.GitRepository._run", forbidden)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, command)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal/hiraku.yml").is_file()
    assert (tmp_path / "minimal/evaluate.py").is_file() == (command[0] == "example")
    assert not (tmp_path / "minimal/.git").exists()


def test_init_without_git_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    assert (
        CliRunner().invoke(main, ["init", "--records-path", "knowledge"]).exit_code == 0
    )
    failed = CliRunner().invoke(main, ["init", "other", "--git"])
    assert failed.exit_code == 1
    assert "not found" in failed.output
    assert not (tmp_path / "other").exists()


def test_init_git_creates_code_repo_and_can_be_repeated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["init", "project", "--git", "--records-path", "knowledge"]
        ).exit_code
        == 0
    )
    root = tmp_path / "project"
    git(root, "config", "hiraku.test", "preserved")
    assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
    assert git(root, "config", "hiraku.test") == "preserved"
    assert not git(root, "ls-files")


def test_git_init_preserves_linked_code_worktree(tmp_path, source):
    checkout = tmp_path / "worktree"
    git(source, "worktree", "add", "-b", "research", str(checkout))
    config = HirakuConfig.init(checkout, records_path="knowledge")
    before = (checkout / ".git").read_bytes()
    init_code_repository(config)
    assert (checkout / ".git").read_bytes() == before
    assert git(checkout, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
