from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task
from hiraku.errors import GitError, LaunchError, ValidationError
from hiraku.git import GitRepository
from hiraku.launch import launch, render_arguments


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


def test_git_resolution_branches_and_worktree_cleanup(tmp_path: Path) -> None:
    config, repository = _repository(tmp_path)
    assert len(repository.resolve("HEAD")) == 40
    assert repository.current_branch is not None
    branch = repository.create_task_branch("demo")
    assert branch == "hiraku/tasks/demo"
    assert repository.branch_exists(branch)
    with pytest.raises(ValidationError):
        repository.validate_branch("bad..branch")

    before = _git(tmp_path, "worktree", "list", "--porcelain")
    with repository.temporary_worktree("HEAD") as worktree:
        assert (worktree / "tracked.txt").read_text() == "committed"
        assert not (worktree / "hiraku.yml").exists()
    assert _git(tmp_path, "worktree", "list", "--porcelain") == before
    assert config.root == tmp_path


def test_git_errors_outside_repository(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(GitError):
        GitRepository(config)


def test_argument_rendering_and_rejections() -> None:
    assert render_arguments(
        {
            "learning_rate": 0.1,
            "verbose": True,
            "quiet": False,
            "empty": None,
            "tag": ["a", "b"],
            "path": Path("input.csv"),
        }
    ) == (
        "--learning-rate",
        "0.1",
        "--verbose",
        "--tag",
        "a",
        "--tag",
        "b",
        "--path",
        "input.csv",
    )
    with pytest.raises(ValidationError):
        render_arguments({"nested": {"bad": True}})  # type: ignore[dict-item]


def test_launch_injects_explicit_attribution_and_overrides(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    experiment = Experiment.create("exp", task=task, config=config)
    entrypoint = {
        "command": [
            "/bin/sh",
            "-c",
            "printf '%s|%s|%s|%s|%s' \"$HIRAKU_TASK_ID\" "
            '"$HIRAKU_EXPERIMENT_ID" "$HIRAKU_ENTRYPOINT" "$1" "$2"',
            "shell",
        ],
        "args": {"seed": 1},
    }
    result = launch(
        entrypoint,
        name="train",
        task_id=task.id,
        experiment_id=experiment.id,
        args={"seed": 2},
        config=config,
    )
    assert result.stdout == "task|exp|train|--seed|2"
    with pytest.raises(ValidationError):
        launch(entrypoint, experiment_id="exp", config=config)
    with pytest.raises(ValidationError):
        launch(
            entrypoint, task_id="task", env={"HIRAKU_TASK_ID": "other"}, config=config
        )


def test_launch_failure_and_commit_isolation(tmp_path: Path) -> None:
    config, _ = _repository(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty")
    result = launch(
        {"command": ["/bin/sh", "-c", "test ! -e dirty.txt"]},
        commit="HEAD",
        config=config,
    )
    assert result.returncode == 0
    with pytest.raises(LaunchError):
        launch({"command": ["/bin/sh", "-c", "exit 7"]}, config=config)


def test_commit_launch_uses_project_subdirectory_inside_repository(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (project / "marker.txt").write_text("project")
    _git(tmp_path, "add", "project/marker.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    config = HirakuConfig.init(project)

    result = launch(
        {"command": ["/bin/sh", "-c", "test -f marker.txt && pwd"]},
        commit="HEAD",
        config=config,
    )
    assert result.stdout.strip().endswith("/project")
