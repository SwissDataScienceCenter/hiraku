from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import ValidationError
from hiraku.project import Project
from hiraku.record import Record, load_record
from hiraku.run import Run


def test_inheritance_and_guarded_delete(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    parent = Record.create(
        "task",
        project=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    child = Record.create(
        "exp",
        parent_id=parent.id,
        project=config,
    )
    effective = child.resolve(project=config)
    assert (
        effective.values["metrics"] == parent.resolve(project=config).values["metrics"]
    )
    assert "command" not in effective.values
    with pytest.raises(ValidationError):
        parent.delete(project=config)
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
    config = Project.create(tmp_path)
    parent = Record.create("parent", commit="a" * 40, project=config)
    child = Record.create("child", parent_id=parent.id, project=config)
    assert "commit" not in child.resolve(project=config).values
    child.commit = "b" * 64
    child.save()
    assert load_record("child", project=config).commit == "b" * 64
    for bad in ("HEAD", "abcdef0", "A" * 40, "g" * 40):
        with pytest.raises((ValidationError, PydanticValidationError)):
            Record(id="bad", commit=bad)


def test_run_immutable_and_missing_csv_empty(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    record = Record.create("record", project=config)
    assert record.runs(project=config) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(PydanticValidationError):
        run.id = "two"  # type: ignore[misc]
