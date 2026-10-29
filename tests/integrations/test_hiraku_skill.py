from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records, load_record
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
    )
    _git(project, "add", ".")
    _git(project, "add", "-f", "hiraku/dispatch")
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


def _create_task(config: HirakuConfig) -> Record:
    return Record.create(
        "heart-disease",
        config=config,
        branch="main",
        goal=(
            "Develop and evaluate predictive models for heart disease on a fixed "
            "Cleveland cohort"
        ),
        question=(
            "Which modeling approaches achieve the best validation log loss and "
            "accuracy on this cohort?"
        ),
        metrics=[
            {"name": "val/log_loss", "direction": "minimize"},
            {"name": "val/accuracy", "direction": "maximize"},
        ],
    )


def _create_logistic_experiment(
    config: HirakuConfig,
    task: Record,
    commit: str,
) -> Record:
    return Record.create(
        "logistic-baseline",
        parent_id=task.id,
        config=config,
        commit=commit,
        summary="Logistic regression baseline",
        hypothesis="Regularized logistic regression provides a calibrated baseline",
        command=[
            sys.executable,
            "train.py",
            "--model",
            "logistic",
            "--seed",
            str(42),
            "--validation-fraction",
            str(0.25),
        ],
    )


def test_agent_creates_task_from_high_level_request(tmp_path: Path) -> None:
    project, config, _ = _new_project(tmp_path)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create a root record named heart-disease.
Its goal is
to develop and evaluate predictive models for heart disease on the fixed
Cleveland cohort without restricting the task to particular model families. Ask
which modeling approaches achieve the best validation log loss and accuracy.
Track val/log_loss to minimize and val/accuracy to maximize. Only create and
verify the record.""",
    )

    tasks = list_records(config=config)
    assert len(tasks) == 1, agent_output
    task = tasks[0]
    assert task.id == "heart-disease"
    assert task.goal is not None
    goal = task.goal.lower()
    assert "predictive" in goal
    assert "heart disease" in goal
    assert "cleveland" in goal
    assert task.question is not None
    question = task.question.lower()
    assert "model" in question
    assert "log loss" in question
    assert "accuracy" in question
    assert [metric.model_dump() for metric in task.metrics] == [
        {"name": "val/log_loss", "direction": "minimize"},
        {"name": "val/accuracy", "direction": "maximize"},
    ]
    assert task.children(config=config) == []
    assert CSVBackend(config).list_runs(record_id=task.id) == []
    assert _git(project, "status", "--porcelain") == ""


def test_agent_creates_experiment_from_high_level_request(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to create a child record named logistic-baseline
under heart-disease. Use the summary 'Logistic regression baseline' and the
hypothesis 'Regularized logistic regression provides a calibrated baseline'. It
should use uv run to invoke train.py with the logistic model, seed 42, and a
validation fraction of 0.25. Bind it to the current commit. Only create and
verify the record; do not run it.""",
    )

    experiments = task.children(config=config)
    assert len(experiments) == 1, agent_output
    experiment = experiments[0]
    assert experiment.id == "logistic-baseline"
    assert experiment.summary == "Logistic regression baseline"
    assert experiment.hypothesis == (
        "Regularized logistic regression provides a calibrated baseline"
    )
    assert experiment.commit == commit
    assert experiment.command == [
        "uv",
        "run",
        "python",
        "train.py",
        "--model",
        "logistic",
        "--seed",
        "42",
        "--validation-fraction",
        "0.25",
    ]
    assert CSVBackend(config).list_runs(record_id=task.id) == []
    assert _git(project, "status", "--porcelain") == ""


def test_agent_runs_existing_experiment_without_other_changes(tmp_path: Path) -> None:
    project, config, commit = _new_project(tmp_path)
    task = _create_task(config)
    experiment = _create_logistic_experiment(config, task, commit)
    task_before = task.path.read_bytes()
    experiment_before = experiment.path.read_bytes()

    agent_output = _run_codex(
        project,
        """Use the $hiraku skill to run the logistic-baseline record and
report its tracked validation metrics. Do not create or update other records.""",
    )

    assert task.path.read_bytes() == task_before, agent_output
    current = load_record(experiment.id, config=config)
    assert current.path.read_bytes() == experiment_before
    assert [item.id for item in task.children(config=config)] == ["logistic-baseline"]
    runs = CSVBackend(config).list_runs(
        record_id=experiment.id,
    )
    assert len(runs) == 1
    run = runs[0]
    assert run.record_id == experiment.id
    assert run.commit == commit
    assert run.id
    assert run.params == {
        "model": "logistic",
        "seed": "42",
        "validation_fraction": "0.25",
    }
    assert float(run.metrics["val/log_loss"]) == pytest.approx(EXPECTED_LOSS)
    assert float(run.metrics["val/accuracy"]) == pytest.approx(EXPECTED_ACCURACY)
    assert _git(project, "status", "--porcelain") == ""
