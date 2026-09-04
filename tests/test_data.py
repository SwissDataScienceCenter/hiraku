from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Run, Task, load_experiment
from hiraku.errors import EntrypointNotFoundError, ValidationError


def test_metrics_entrypoints_inheritance_and_guarded_delete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create(
        "task",
        config=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    experiment = Experiment.create("exp", task=task, config=config)
    assert experiment.is_proposal
    assert experiment.metrics == task.metrics
    assert task.entrypoint("train").command == ["python", "train.py"]
    with pytest.raises(EntrypointNotFoundError):
        experiment.entrypoint("train")
    with pytest.raises(ValidationError):
        task.delete(config=config)

    with pytest.raises(PydanticValidationError):
        Task(id="invalid", metrics=[{"name": "loss", "direction": "sideways"}])


def test_experiment_commit_is_passive_metadata_without_git(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    first_hash = "a" * 40
    experiment = Experiment.create("exp", task=task, commit=first_hash, config=config)

    assert experiment.commit == first_hash
    assert load_experiment("exp", task=task, config=config).commit == first_hash

    second_hash = "b" * 64
    experiment.commit = second_hash
    experiment.save()
    assert load_experiment("exp", task=task, config=config).commit == second_hash


@pytest.mark.parametrize(
    "commit",
    [
        "HEAD",
        "abcdef0",
        "a" * 39,
        "a" * 41,
        "a" * 63,
        "a" * 65,
        "A" * 40,
        "g" * 40,
        123,
    ],
)
def test_experiment_commit_requires_a_full_lowercase_hash(
    tmp_path: Path, commit: object
) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    with pytest.raises(ValidationError, match="full lowercase Git hash"):
        Experiment(id="exp", task_id=task.id, commit=commit)  # type: ignore[arg-type]


def test_run_is_immutable_and_missing_csv_is_empty(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    assert task.runs(config=config) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(PydanticValidationError):
        run.id = "two"  # type: ignore[misc]
