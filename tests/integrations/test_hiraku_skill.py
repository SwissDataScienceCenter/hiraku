from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task, list_experiments, list_tasks, load_experiment
from hiraku.tracking import CSVBackend

RUN_AGENT_TESTS = os.environ.get("HIRAKU_RUN_CODEX_SKILL_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_AGENT_TESTS,
    reason="set HIRAKU_RUN_CODEX_SKILL_TESTS=1 to run Codex skill tests",
)

MODEL = "gpt-5.6-luna"
TIMEOUT_SECONDS = 300
EXPECTED_LOSS = 0.32254890
EXPECTED_ACCURACY = 0.85526316


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _new_project(tmp_path: Path) -> tuple[Path, HirakuConfig, str]:
    project = tmp_path / "heart-disease"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "user.name", "Test")
    config = HirakuConfig.init(
        project,
        example="heart-disease",
        ignore_git=False,
    )
    _git(project, "add", ".")
    _git(project, "commit", "-m", "initialize Heart Disease example")
    commit = _git(project, "rev-parse", "HEAD")
    return project, config, commit


def _run_codex(project: Path, prompt: str) -> str:
    codex = shutil.which("codex")
    hiraku = shutil.which("hiraku")
    if codex is None:
        pytest.fail("codex is not on PATH; install and authenticate the Codex CLI")
    if hiraku is None:
        pytest.fail("hiraku is not on PATH; run this test through `uv run pytest`")

    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), environment.get("PATH", "")]
    )
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--model",
        MODEL,
        "--config",
        'model_reasoning_effort="low"',
        "--approve-for-me",
        "--color",
        "never",
        "--cd",
        str(project),
        prompt,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"Codex {MODEL} timed out after {TIMEOUT_SECONDS} seconds.\n"
            f"stdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        )
    if result.returncode != 0:
        pytest.fail(
            f"Codex {MODEL} exited with status {result.returncode}. "
            "Check local authentication and model availability.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


def _create_task(config: HirakuConfig) -> Task:
    return Task.create(
        "heart-disease",
        config=config,
        branch="main",
        goal="Compare two model families on a fixed Cleveland cohort",
        question=(
            "Does a nonlinear random forest improve on a calibrated linear baseline?"
        ),
        metrics=[
            {"name": "val/log_loss", "direction": "minimize"},
            {"name": "val/accuracy", "direction": "maximize"},
        ],
    )


def _create_logistic_experiment(
    config: HirakuConfig,
    task: Task,
    commit: str,
) -> Experiment:
    return Experiment.create(
        "logistic-baseline",
        task=task,
        config=config,
        commit=commit,
        summary="Logistic regression baseline",
        hypothesis="Regularized logistic regression provides a calibrated baseline",
        entrypoints={
            "train": {
                "command": [sys.executable, "train.py"],
                "args": {
                    "model": "logistic",
                    "seed": 42,
                    "validation-fraction": 0.25,
                },
            }
        },
    )


def test_agent_creates_task_from_high_level_request(tmp_path: Path) -> None:
    project, config, _ = _new_project(tmp_path)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create a task named heart-disease. Its goal is
to compare logistic regression and random forest on the fixed Cleveland cohort.
Ask whether the nonlinear model improves on the calibrated linear baseline.
Track validation log loss to minimize and validation accuracy to maximize. Only
create and verify the task.""",
    )

    tasks = list_tasks(config=config)
    assert len(tasks) == 1, agent_output
    task = tasks[0]
    assert task.id == "heart-disease"
    assert task.goal is not None
    goal = task.goal.lower()
    assert "logistic regression" in goal
    assert "random forest" in goal
    assert "cleveland" in goal
    assert task.question is not None
    question = task.question.lower()
    assert "nonlinear" in question
    assert "calibrated" in question
    assert "baseline" in question
    assert [metric.model_dump() for metric in task.metrics] == [
        {"name": "val/log_loss", "direction": "minimize"},
        {"name": "val/accuracy", "direction": "maximize"},
    ]
    assert list_experiments(task=task, config=config) == []
    assert CSVBackend(config).list_runs(task_id=task.id) == []
    assert _git(project, "status", "--porcelain") == ""


def test_agent_creates_experiment_from_high_level_request(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create an experiment named logistic-baseline
for the active task. Use the summary 'Logistic regression baseline' and the
hypothesis 'Regularized logistic regression provides a calibrated baseline'. It
should run train.py with the logistic model, seed 42, and a validation fraction
of 0.25. Bind it to the current commit. Only create and verify the experiment;
do not run it.""",
    )

    experiments = list_experiments(task=task, config=config)
    assert len(experiments) == 1, agent_output
    experiment = experiments[0]
    assert experiment.id == "logistic-baseline"
    assert experiment.summary == "Logistic regression baseline"
    assert experiment.hypothesis == (
        "Regularized logistic regression provides a calibrated baseline"
    )
    assert experiment.commit == commit
    assert list(experiment.entrypoints) == ["train"]
    entrypoint = experiment.entrypoints["train"]
    assert entrypoint.command[-1] == "train.py"
    normalized_args = {
        key.replace("_", "-"): value for key, value in entrypoint.args.items()
    }
    assert normalized_args == {
        "model": "logistic",
        "seed": 42,
        "validation-fraction": 0.25,
    }
    assert CSVBackend(config).list_runs(task_id=task.id) == []
    assert _git(project, "status", "--porcelain") == ""


def test_agent_runs_existing_experiment_without_other_changes(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)
    experiment = _create_logistic_experiment(config, task, commit)
    task_before = task.path.read_bytes()
    experiment_before = experiment.path.read_bytes()

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to run the logistic-baseline experiment and
report its tracked validation metrics. Do not create or update other records.""",
    )

    assert task.path.read_bytes() == task_before, agent_output
    current = load_experiment(experiment.id, task=task, config=config)
    assert current.path.read_bytes() == experiment_before
    assert [item.id for item in list_experiments(task=task, config=config)] == [
        "logistic-baseline"
    ]
    runs = CSVBackend(config).list_runs(
        task_id=task.id,
        experiment_id=experiment.id,
    )
    assert len(runs) == 1
    run = runs[0]
    assert run.task_id == task.id
    assert run.experiment_id == experiment.id
    assert run.commit == commit
    assert run.entrypoint == "train"
    assert run.params == {
        "model": "logistic",
        "seed": "42",
        "validation_fraction": "0.25",
    }
    assert float(run.metrics["val/log_loss"]) == pytest.approx(EXPECTED_LOSS)
    assert float(run.metrics["val/accuracy"]) == pytest.approx(EXPECTED_ACCURACY)
    assert _git(project, "status", "--porcelain") == ""
