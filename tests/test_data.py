from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record, open_workspace
from hiraku.run import Run


def test_local_metrics_and_guarded_delete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    parent = Record.create(
        workspace,
        "task/task.md",
        id="task",
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    config.set_active_record(parent)
    child = Record.create(workspace, "exp/exp.md", id="exp", parent_id=parent.id)
    config.set_active_record(child)
    assert child.metrics is None
    with pytest.raises(ValidationError):
        parent.delete(open_workspace(config))
    with pytest.raises(PydanticValidationError):
        Record(id="invalid", metrics=[{"name": "loss", "direction": "sideways"}])
    with pytest.raises(PydanticValidationError):
        Record(
            id="invalid",
            metrics=[{"name": "loss", "direction": "minimize", "unit": "s"}],
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("command", ["python", "train.py"]),
        ("entrypoints", {"train": {"command": ["python"]}}),
    ],
)
def test_removed_launch_fields_are_not_record_schema_fields(name, value) -> None:
    assert name not in Record.model_fields
    record = Record(id="root", **{name: value})
    assert record.model_extra[name] == value


def test_commit_local_and_validated(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    parent = Record.create(workspace, "parent/parent.md", id="parent", commit="a" * 40)
    config.set_active_record(parent)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=parent.id)
    config.set_active_record(child)
    assert child.commit is None
    child.commit = "b" * 64
    child.save(open_workspace(config))
    assert Record.load(open_workspace(config), "child").commit == "b" * 64
    for bad in ("HEAD", "abcdef0", "A" * 40, "g" * 40):
        with pytest.raises((ValidationError, PydanticValidationError)):
            Record(id="bad", commit=bad)


def test_run_immutable_and_missing_csv_empty(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    record = Record.create(workspace, "record/record.md", id="record")
    config.set_active_record(record)
    assert record.runs(config=config) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(PydanticValidationError):
        run.id = "two"  # type: ignore[misc]
