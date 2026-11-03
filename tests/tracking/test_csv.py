from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.record import Record
from hiraku.tracking.csv import CSVBackend, CSVBackendConfig


def test_csv_backend_config_is_validated() -> None:
    config = CSVBackendConfig.model_validate({"type": "csv", "path": "runs.csv"})
    assert config.path == Path("runs.csv")


def test_csv_keeps_historical_entrypoint_as_raw_metadata(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,entrypoint\nnew,record,\nold,record,train\n"
    )
    runs = {run.id: run for run in CSVBackend(config).list_runs()}
    assert runs["new"].id == "new"
    assert runs["old"].raw["entrypoint"] == "train"


def test_csv_backend_reads_job_attribution(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,job_id\nrun-1,record,job-1\n", encoding="utf-8"
    )

    run = CSVBackend(config).list_runs()[0]

    assert run.job_id == "job-1"


def test_csv_backend_normalizes_runs_and_sorts(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("iris", config=config)
    path = config.root / "runs.csv"
    path.write_text(
        "id,record_id,timestamp,metric.accuracy,param.seed,note\n"
        "a,iris,2026-01-02T00:00:00Z,0.8,1,kept\n"
        "b,iris,2026-01-01T00:00:00Z,0.9,2,raw\n",
        encoding="utf-8",
    )

    runs = CSVBackend(config).list_runs(
        record_id="iris", sort="accuracy", ascending=False
    )
    assert [run.id for run in runs] == ["b", "a"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"accuracy": "0.9"}
    assert runs[0].params == {"seed": "2"}
    assert runs[0].raw == {"note": "raw"}


def test_csv_backend_reads_duration_in_seconds(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("iris", config=config)
    (config.root / "runs.csv").write_text(
        "id,record_id,timestamp,duration\n"
        "a,iris,2026-01-01T00:00:00Z,4.25\n"
        "b,iris,2026-01-01T00:01:00Z,\n",
        encoding="utf-8",
    )

    runs = CSVBackend(config).list_runs(record_id="iris")

    assert [run.duration for run in runs] == [4.25, None]
    assert runs[0].raw == {}


@pytest.mark.parametrize("value", ["-1", "nan", "soon"])
def test_csv_backend_rejects_invalid_duration(tmp_path: Path, value: str) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("iris", config=config)
    (config.root / "runs.csv").write_text(
        f"id,record_id,duration\na,iris,{value}\n", encoding="utf-8"
    )

    with pytest.raises(BackendError, match="malformed row"):
        CSVBackend(config).list_runs(record_id="iris")


def test_csv_source_listing_returns_all_attributed_runs(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    path = config.root / "runs.csv"
    path.write_text("id,record_id\none,first\ntwo,second\nthree,\n", encoding="utf-8")

    runs = CSVBackend(config).list_runs()

    assert {run.id for run in runs} == {"one", "two"}
    assert {run.id for run in CSVBackend(config).list_runs()} == {
        "one",
        "two",
    }


def test_csv_backend_resolves_record_path_from_project_root(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    config.tracking_backend = {"type": "csv", "path": "results/iris.csv"}
    record = Record.create("iris", config=config)
    path = tmp_path / "results" / "iris.csv"
    path.parent.mkdir()
    path.write_text("id,record_id\na,iris\n", encoding="utf-8")

    runs = record.runs(config=config)

    assert [run.id for run in runs] == ["a"]


def test_csv_backend_preserves_absolute_path_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = HirakuConfig.init(project)
    path = tmp_path / "external.csv"
    path.write_text("id,record_id\na,iris\n", encoding="utf-8")

    runs = CSVBackend(config, path=path).list_runs(record_id="iris")

    assert [run.id for run in runs] == ["a"]


def test_csv_backend_requires_record_id_column(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    (tmp_path / "runs.csv").write_text("id,note\na,missing\n")
    with pytest.raises(BackendError, match="no record_id column"):
        CSVBackend(config).list_runs(record_id="child")
