from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import ValidationError
from hiraku.project import Project
from hiraku.tracking import CSVBackend, Run


def test_run_duration_is_parsed_as_seconds() -> None:
    assert Run(id="a", duration="4.5").duration == 4.5
    assert Run(id="b").duration is None


def test_run_id_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="at least 1 character"):
        Run(id="")


def test_run_keeps_backend_metadata_in_raw() -> None:
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        Run(id="a", backend_state="finished")
    assert Run(id="a", raw={"backend_state": "finished"}).raw == {
        "backend_state": "finished"
    }


def test_run_mapping_fields_are_shallow_immutable_snapshots() -> None:
    metrics = {"result": 1.0}
    raw = {"details": ["original"]}
    run = Run(id="a", metrics=metrics, raw=raw)

    metrics["later"] = 2.0
    raw["other"] = "added"
    assert "later" not in run.metrics
    assert "other" not in run.raw
    with pytest.raises(TypeError):
        run.metrics["later"] = 2.0  # type: ignore[index]
    raw["details"].append("nested mutation")
    assert run.raw["details"] == ["original", "nested mutation"]


@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf])
def test_run_rejects_invalid_duration(value: float) -> None:
    with pytest.raises(ValidationError, match="duration"):
        Run(id="a", duration=value)


def test_run_immutable_and_missing_csv_empty(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    record = config.vault.create("record/record", type="task", title="record")
    assert CSVBackend(config, path="runs.csv").list_runs(record_id=record.id) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(PydanticValidationError):
        run.id = "two"  # type: ignore[misc]
