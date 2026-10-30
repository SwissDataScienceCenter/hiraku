from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record, load_record
from hiraku.run import Run


def test_inheritance_and_guarded_delete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    parent = Record.create(
        "task",
        config=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    child = Record.create(
        "exp",
        parent_id=parent.id,
        config=config,
    )
    effective = child.resolve(config=config)
    assert (
        effective.values["metrics"] == parent.resolve(config=config).values["metrics"]
    )
    assert "command" not in effective.values
    with pytest.raises(ValidationError):
        parent.delete(config=config)
    with pytest.raises(PydanticValidationError):
        Record(id="invalid", metrics=[{"name": "loss", "direction": "sideways"}])


def test_record_rejects_command_metadata(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(ValidationError, match="command is not a record field"):
        Record.create("root", config=config, command=["python", "train.py"])


def test_commit_local_and_validated(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    parent = Record.create("parent", commit="a" * 40, config=config)
    child = Record.create("child", parent_id=parent.id, config=config)
    assert "commit" not in child.resolve(config=config).values
    child.commit = "b" * 64
    child.save()
    assert load_record("child", config=config).commit == "b" * 64
    for bad in ("HEAD", "abcdef0", "A" * 40, "g" * 40):
        with pytest.raises((ValidationError, PydanticValidationError)):
            Record(id="bad", commit=bad)


def test_run_immutable_and_missing_csv_empty(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    record = Record.create("record", config=config)
    assert record.runs(config=config) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(PydanticValidationError):
        run.id = "two"  # type: ignore[misc]
