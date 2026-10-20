from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import GitError, ValidationError
from hiraku.git import GitRepository, init_code_repository, setup, status


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
def project(tmp_path):
    return HirakuConfig.init(tmp_path / "project")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    git(path, "init", "-q")
    (path / "README.md").write_text("Research history\n")
    commit(path)
    return path


def test_init_does_not_invoke_git(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Git must not be invoked during plain initialization")

    monkeypatch.setattr("hiraku.git.GitRepository._execute", forbidden)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", "--example", "minimal"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal/evaluate.py").is_file()
    assert not (tmp_path / "minimal/.git").exists()


def test_init_without_git_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(main, ["init"]).exit_code == 0
    failed = CliRunner().invoke(main, ["init", "other", "--git"])
    assert failed.exit_code == 1
    assert "not found" in failed.output
    assert not (tmp_path / "other").exists()


def test_init_git_creates_code_repo_and_can_be_repeated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
    root = tmp_path / "project"
    git(root, "config", "hiraku.test", "preserved")
    assert runner.invoke(main, ["init", "project", "--git"]).exit_code == 0
    assert git(root, "config", "hiraku.test") == "preserved"
    assert not git(root, "ls-files")


@pytest.mark.parametrize("installed", [False, True])
def test_ignore_preserves_rules_and_is_repeatable(project, monkeypatch, installed):
    if not installed:
        monkeypatch.setattr("shutil.which", lambda name: None)
    ignore = project.root / ".gitignore"
    ignore.write_text("# project rules\n/data/")
    setup(project, "ignore")
    first = ignore.read_bytes()
    setup(project, "ignore")
    assert ignore.read_bytes() == first
    assert first.startswith(b"# project rules\n/data/\n")
    assert b"/hiraku/\n" in first and b"/hiraku.yml\n" in first
    assert status(project)["mode"] == "ignore"
    assert not (project.root / ".git").exists()


def test_repo_has_independent_history_without_automatic_commit(project):
    setup(project, "repo")
    setup(project, "repo")
    assert status(project)["mode"] == "repo"
    assert git(project.hiraku_dir, "rev-parse", "--show-toplevel") == str(
        project.hiraku_dir
    )
    assert not git(project.hiraku_dir, "ls-files")
    init_code_repository(project)
    assert git(project.root, "check-ignore", "hiraku/", "hiraku.yml")
    sha = commit(project.hiraku_dir)
    setup(project, "repo")
    assert git(project.hiraku_dir, "rev-parse", "HEAD") == sha


@pytest.mark.parametrize("mode", ["ignore", "repo", "submodule"])
def test_setup_refuses_to_untrack_existing_files(project, source, mode):
    init_code_repository(project)
    git(project.root, "add", "hiraku.yml")
    before = (project.root / ".git/index").read_bytes()
    with pytest.raises(ValidationError, match="already tracked"):
        setup(project, mode, url=str(source) if mode == "submodule" else None)
    assert not (project.root / ".gitignore").exists()
    assert (project.root / ".git/index").read_bytes() == before
    assert not (project.hiraku_dir / ".git").exists()


def test_submodule_from_local_source_preserves_files_and_staged_changes(
    project, source
):
    init_code_repository(project)
    (project.root / "code.py").write_text("print(1)")
    git(project.root, "add", "code.py")
    setup(project, "repo")
    sha = commit(project.hiraku_dir)
    (project.hiraku_dir / "notes.md").write_text("Local findings\n")
    setup(project, "submodule", url=str(source))
    assert status(project)["mode"] == "submodule"
    assert not (project.hiraku_dir / "README.md").exists()
    assert (project.hiraku_dir / "notes.md").read_text() == "Local findings\n"
    assert git(project.hiraku_dir, "rev-parse", "HEAD") == sha
    assert git(project.root, "ls-files") == ".gitmodules\ncode.py\nhiraku"
    assert (
        git(project.root, "config", "-f", ".gitmodules", "submodule.hiraku.ignore")
        == "all"
    )
    index = (project.root / ".git/index").read_bytes()
    commit(project.hiraku_dir)
    setup(project, "submodule", url=str(source))
    assert (project.root / ".git/index").read_bytes() == index
    assert "/hiraku/\n" not in (project.root / ".gitignore").read_text()
    assert "/hiraku.yml\n" in (project.root / ".gitignore").read_text()


def test_submodule_conversion_preserves_existing_history(project, source):
    init_code_repository(project)
    setup(project, "repo")
    sha = commit(project.hiraku_dir)
    setup(project, "submodule", url=str(source))
    assert git(project.hiraku_dir, "rev-parse", "HEAD") == sha
    assert status(project)["mode"] == "submodule"
    with pytest.raises(ValidationError, match="already a submodule"):
        setup(project, "repo")
    with pytest.raises(ValidationError, match="different URL"):
        setup(project, "submodule", url="another-source")


@pytest.mark.parametrize(
    "url", ["https://example.invalid/records.git", "missing-local-repo"]
)
def test_submodule_records_url_without_accessing_it(project, monkeypatch, url):
    init_code_repository(project)
    setup(project, "repo")
    sha = commit(project.hiraku_dir)
    original = GitRepository._execute

    def offline(arguments, **kwargs):
        assert not any(
            arg in {"clone", "fetch", "push", "pull", "ls-remote"} for arg in arguments
        )
        return original(arguments, **kwargs)

    monkeypatch.setattr(GitRepository, "_execute", staticmethod(offline))
    setup(project, "submodule", url=url)
    assert git(project.hiraku_dir, "rev-parse", "HEAD") == sha
    expected = url if "://" in url else str(Path(url).resolve())
    assert (
        git(project.root, "config", "-f", ".gitmodules", "submodule.hiraku.url")
        == expected
    )


def test_submodule_requires_existing_metadata_repository(project, source):
    init_code_repository(project)
    (project.hiraku_dir / "README.md").write_text("Local work\n")
    with pytest.raises(ValidationError, match="setup repo"):
        setup(project, "submodule", url=str(source))
    assert (project.hiraku_dir / "README.md").read_text() == "Local work\n"
    assert not (project.hiraku_dir / ".git").exists()
    assert not (project.root / ".gitmodules").exists()
    assert not (project.root / ".gitignore").exists()
    assert not list(project.root.glob(".hiraku-git-*"))


def test_submodule_requires_url_code_repo_and_metadata_commit(project, tmp_path):
    with pytest.raises(ValidationError, match="requires --url"):
        setup(project, "submodule")
    with pytest.raises(ValidationError, match="code repository"):
        setup(project, "submodule", url="missing")
    init_code_repository(project)
    with pytest.raises(ValidationError, match="setup repo"):
        setup(project, "submodule", url=str(tmp_path / "missing"))
    setup(project, "repo")
    with pytest.raises(ValidationError, match="Commit metadata first"):
        setup(project, "submodule", url="missing")
    assert (project.hiraku_dir / ".git").is_dir()
    assert not (project.root / ".gitmodules").exists()


@pytest.mark.parametrize("mode", ["ignore", "repo", "submodule"])
def test_cli_setup_and_status_from_nested_directory(project, source, monkeypatch, mode):
    if mode == "submodule":
        init_code_repository(project)
        setup(project, "repo")
        commit(project.hiraku_dir)
    nested = project.root / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    runner = CliRunner()
    arguments = ["git", "setup", mode]
    if mode == "submodule":
        arguments.extend(["--url", str(source)])
    setup_result = runner.invoke(main, arguments)
    assert setup_result.exit_code == 0, setup_result.output
    result = runner.invoke(main, ["git", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == mode
    invalid = runner.invoke(main, ["git", "setup", "repo", "--url", "unexpected"])
    assert invalid.exit_code == 1
    assert "only supported" in invalid.output


def test_custom_metadata_directory(project):
    original = project.hiraku_dir
    project.hiraku_dir_name = "research/metadata"
    project.save()
    project.hiraku_dir.parent.mkdir()
    original.rename(project.hiraku_dir)
    setup(project, "repo")
    assert "/research/metadata/\n" in (project.root / ".gitignore").read_text()
    assert status(project)["mode"] == "repo"


def test_failed_registration_restores_files_and_index(project, source, monkeypatch):
    init_code_repository(project)
    (project.root / "code.py").write_text("staged code")
    git(project.root, "add", "code.py")
    setup(project, "repo")
    commit(project.hiraku_dir)
    index = (project.root / ".git/index").read_bytes()
    config = (project.root / ".git/config").read_bytes()
    before = {
        p.relative_to(project.root): p.read_bytes()
        for p in project.hiraku_dir.rglob("*")
        if p.is_file()
    }
    original = GitRepository._git

    def fail(self, *args, **kwargs):
        if args == ("add", "--", ".gitmodules"):
            raise GitError("injected registration failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitRepository, "_git", fail)
    with pytest.raises(GitError, match="injected"):
        setup(project, "submodule", url=str(source))
    assert (project.root / ".git/index").read_bytes() == index
    assert (project.root / ".git/config").read_bytes() == config
    assert not (project.root / ".gitmodules").exists()
    assert {
        p.relative_to(project.root): p.read_bytes()
        for p in project.hiraku_dir.rglob("*")
        if p.is_file()
    } == before


def test_submodule_rejects_uncommitted_gitmodules(project, source):
    init_code_repository(project)
    setup(project, "repo")
    commit(project.hiraku_dir)
    modules = project.root / ".gitmodules"
    modules.write_text("# existing work\n")
    with pytest.raises(ValidationError, match="existing .gitmodules"):
        setup(project, "submodule", url=str(source))
    assert modules.read_text() == "# existing work\n"
    assert (project.hiraku_dir / ".git").exists()


def test_git_init_preserves_linked_code_worktree(tmp_path, source):
    checkout = tmp_path / "worktree"
    git(source, "worktree", "add", "-b", "research", str(checkout))
    config = HirakuConfig.init(checkout)
    before = (checkout / ".git").read_bytes()
    init_code_repository(config)
    assert (checkout / ".git").read_bytes() == before
    assert git(checkout, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")


@pytest.mark.parametrize("mode", ["ignore", "repo"])
def test_setup_rejects_symlink_ignore_without_changes(project, tmp_path, mode):
    target = tmp_path / "ignore"
    target.write_text("keep this")
    (project.root / ".gitignore").symlink_to(target)
    with pytest.raises(ValidationError, match="symbolic-link"):
        setup(project, mode)
    assert target.read_text() == "keep this"
    assert not (project.hiraku_dir / ".git").exists()
