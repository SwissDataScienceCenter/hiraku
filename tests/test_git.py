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


class TestGitRepositoryBehavior:
    def _repository(self, path: Path) -> tuple[Project, GitRepository]:
        _git(path, "init", "-q")
        _git(path, "config", "user.email", "test@example.invalid")
        _git(path, "config", "user.name", "Test")
        (path / "tracked.txt").write_text("committed", encoding="utf-8")
        _git(path, "add", "tracked.txt")
        _git(path, "commit", "-qm", "initial")
        project = Project.create(path)
        return project, GitRepository(project.root)

    def test_ref_and_branch_follow_repository_state(self, tmp_path: Path) -> None:
        project, repository = self._repository(tmp_path)
        sha = repository.resolve("HEAD")

        assert len(sha) == 40
        assert repository.current_branch is not None
        _git(tmp_path, "switch", "-c", "research")
        assert repository.current_branch == "research"
        assert repository.resolve("research") == sha
        assert project.root == tmp_path

    def test_detached_head_has_no_branch(self, tmp_path: Path) -> None:
        _, repository = self._repository(tmp_path)
        sha = repository.resolve("HEAD")

        _git(tmp_path, "checkout", "--detach", sha)

        assert repository.current_branch is None
        assert repository.resolve("HEAD") == sha

    @pytest.mark.parametrize("ref", ["", "missing", "--invalid"])
    def test_invalid_commit_reference_raises(self, tmp_path: Path, ref: str) -> None:
        _, repository = self._repository(tmp_path)

        with pytest.raises(GitError):
            repository.resolve(ref)

    def test_directory_outside_repository_is_rejected(self, tmp_path: Path) -> None:
        project = Project.create(tmp_path)

        with pytest.raises(GitError):
            GitRepository(project.root)

    @pytest.fixture
    def source(self, tmp_path: Path) -> Path:
        path = tmp_path / "source"
        path.mkdir()
        _git(path, "init", "-q")
        (path / "README.md").write_text("Research history\n", encoding="utf-8")
        _git(path, "config", "user.name", "Test")
        _git(path, "config", "user.email", "test@example.invalid")
        _git(path, "add", ".")
        _git(path, "commit", "-qm", "Initial metadata")
        return path

    @pytest.mark.parametrize("command", [["init", "minimal"]])
    def test_plain_project_creation_does_not_invoke_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
    ) -> None:
        def forbidden(*args, **kwargs):
            pytest.fail("Git must not be invoked during plain initialization")

        monkeypatch.setattr("hiraku.git.GitRepository._run", forbidden)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, command)

        assert result.exit_code == 0, result.output
        assert (tmp_path / "minimal/hiraku.yml").is_file()
        assert (tmp_path / "minimal/evaluate.py").is_file() == (command[0] == "example")
        assert not (tmp_path / "minimal/.git").exists()

    def test_requested_git_reports_missing_executable_without_creating_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        assert runner.invoke(main, ["init"]).exit_code == 0
        failed = runner.invoke(main, ["init", "other", "--git"])

        assert failed.exit_code == 1
        assert "not found" in failed.output
        assert not (tmp_path / "other").exists()

    def test_git_initialization_is_repeatable_and_preserves_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
        root = tmp_path / "project"
        _git(root, "config", "hiraku.test", "preserved")
        assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0

        assert _git(root, "config", "hiraku.test") == "preserved"
        assert not _git(root, "ls-files")

    def test_git_initialization_preserves_linked_worktree(
        self, tmp_path: Path, source: Path
    ) -> None:
        checkout = tmp_path / "worktree"
        _git(source, "worktree", "add", "-b", "research", str(checkout))
        project = Project.create(checkout)
        git_file = (checkout / ".git").read_bytes()

        init_code_repository(project)

        assert (checkout / ".git").read_bytes() == git_file
        assert _git(checkout, "rev-parse", "HEAD") == _git(source, "rev-parse", "HEAD")
