from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task
from hiraku.errors import LaunchError, ValidationError
from hiraku.launch import launch, render_arguments


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> HirakuConfig:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("committed")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    config = HirakuConfig.init(tmp_path)
    Task.create("task", config=config)
    return config


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
        capture_output=True,
        config=config,
    )
    assert result.stdout == "task|exp|train|--seed|2"
    with pytest.raises(TypeError):
        launch(entrypoint, experiment_id="exp", config=config)
    with pytest.raises(ValidationError):
        launch(
            entrypoint,
            task_id="task",
            env={"HIRAKU_TASK_ID": "other"},
            config=config,
        )


def test_launch_failure_and_explicit_worktree_isolation(tmp_path: Path) -> None:
    config = _repository(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty")
    result = launch(
        {"command": ["/bin/sh", "-c", "test -e dirty.txt"]},
        task_id="task",
        commit="HEAD",
        capture_output=True,
        config=config,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    isolated = launch(
        {"command": ["/bin/sh", "-c", "test ! -e dirty.txt"]},
        task_id="task",
        commit="HEAD",
        dispatch="worktree",
        capture_output=True,
        config=config,
    )
    assert isolated.returncode == 0
    with pytest.raises(LaunchError):
        launch(
            {"command": ["/bin/sh", "-c", "exit 7"]},
            task_id="task",
            config=config,
        )


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
    Task.create("task", config=config)
    result = launch(
        {"command": ["/bin/sh", "-c", "test -f marker.txt && pwd"]},
        task_id="task",
        commit="HEAD",
        dispatch="worktree",
        capture_output=True,
        config=config,
    )
    assert result.stdout.strip().endswith("/project")


def test_dispatcher_receives_context_and_experiment_commit(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    experiment = Experiment.create("exp", task=task, commit="a" * 40, config=config)
    dispatcher = config.dispatch_dir / "inspect.sh"
    dispatcher.write_text(
        "#!/bin/sh\n"
        "printf '%s|%s|%s|%s|%s|%s|%s|%s\\n' \"$HIRAKU_TASK_ID\" "
        '"$HIRAKU_EXPERIMENT_ID" "$HIRAKU_TASK_FILE" '
        '"$HIRAKU_EXPERIMENT_FILE" "$HIRAKU_ENTRYPOINT" '
        '"$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE" "$HIRAKU_ROOT"\n'
        "printf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    dispatcher.chmod(0o755)
    config.default_dispatch = "inspect.sh"

    result = launch(
        {"command": ["python", "train.py"]},
        name="train",
        task_id=task.id,
        experiment_id=experiment.id,
        capture_output=True,
        config=config,
    )

    assert result.stdout.splitlines() == [
        "|".join(
            (
                "task",
                "exp",
                str(task.path.resolve()),
                str(experiment.path.resolve()),
                "train",
                "",
                "unavailable",
                str(config.root.resolve()),
            )
        ),
        "--commit",
        "a" * 40,
        "--",
        "python",
        "train.py",
    ]

    overridden = launch(
        {"command": ["python", "train.py"]},
        task_id=task.id,
        experiment_id=experiment.id,
        commit="HEAD",
        capture_output=True,
        config=config,
    )
    assert overridden.stdout.splitlines()[1:3] == ["--commit", "HEAD"]
