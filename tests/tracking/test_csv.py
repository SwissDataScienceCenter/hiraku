from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from hiraku.errors import BackendError
from hiraku.project import Project
from hiraku.tracking import CSVBackend, CSVBackendConfig


def test_csv_backend_config_is_validated() -> None:
    config = CSVBackendConfig.model_validate({"type": "csv", "path": "runs.csv"})
    assert config.path == Path("runs.csv")


def test_csv_backend_normalizes_runs_and_sorts(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("iris/iris", type="task", title="iris")
    )
    path = config.root / "runs.csv"
    path.write_text(
        "id,record_id,timestamp,metric.accuracy,param.seed,note\n"
        "a,iris/iris,2026-01-02T00:00:00Z,0.8,1,kept\n"
        "b,iris/iris,2026-01-01T00:00:00Z,0.9,2,raw\n",
        encoding="utf-8",
    )

    runs = CSVBackend(config).list_runs(
        record_id="iris/iris", sort="accuracy", ascending=False
    )
    assert [run.id for run in runs] == ["b", "a"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"accuracy": 0.9}
    assert runs[0].params == {"seed": "2"}
    assert runs[0].raw == {"note": "raw"}


def test_csv_backend_reads_duration_in_seconds(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("iris/iris", type="task", title="iris")
    )
    (config.root / "runs.csv").write_text(
        "id,record_id,timestamp,duration\n"
        "a,iris/iris,2026-01-01T00:00:00Z,4.25\n"
        "b,iris/iris,2026-01-01T00:01:00Z,\n",
        encoding="utf-8",
    )

    runs = CSVBackend(config).list_runs(record_id="iris/iris")

    assert [run.duration for run in runs] == [4.25, None]
    assert runs[0].raw == {}


@pytest.mark.parametrize("value", ["-1", "nan", "soon"])
def test_csv_backend_rejects_invalid_duration(tmp_path: Path, value: str) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("iris/iris", type="task", title="iris")
    )
    (config.root / "runs.csv").write_text(
        f"id,record_id,duration\na,iris/iris,{value}\n", encoding="utf-8"
    )

    with pytest.raises(BackendError, match="malformed row"):
        CSVBackend(config).list_runs(record_id="iris/iris")


def test_csv_source_listing_can_include_unattributed_runs(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    path = config.root / "runs.csv"
    path.write_text("id,record_id\none,first\ntwo,second\nthree,\n", encoding="utf-8")

    runs = CSVBackend(config).list_runs()

    assert {run.id for run in runs} == {"one", "two"}
    assert {
        run.id for run in CSVBackend(config).list_runs(include_unattributed=True)
    } == {
        "one",
        "two",
        "three",
    }


def test_csv_backend_resolves_record_id_from_project_root(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    source = {"type": "csv", "path": "results/iris.csv"}
    record = config.vault.create("iris/iris", type="task", title="iris")
    config.set_active_record(record)
    path = tmp_path / "results" / "iris.csv"
    path.parent.mkdir()
    path.write_text("id,record_id\na,iris/iris\n", encoding="utf-8")

    runs = CSVBackend(config, path=source["path"]).list_runs(record_id=record.id)

    assert [run.id for run in runs] == ["a"]


def test_tracking_filters_exact_concept_ids(tmp_path):
    project = Project.create(tmp_path, install_skill=False)
    project.vault.create("data/example", type="Dataset")
    (tmp_path / "runs.csv").write_text(
        "id,record_id\na,data/example\nb,other/concept\n"
    )
    backend = CSVBackend(project)
    assert [run.id for run in backend.list_runs(record_id="data/example")] == ["a"]
    assert [run.record_id for run in backend.list_runs()] == [
        "data/example",
        "other/concept",
    ]


def test_csv_backend_preserves_absolute_path_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = Project.create(project)
    path = tmp_path / "external.csv"
    path.write_text("id,record_id\na,iris\n", encoding="utf-8")

    runs = CSVBackend(config, path=path).list_runs(record_id="iris")

    assert [run.id for run in runs] == ["a"]


def test_csv_backend_accepts_missing_record_id_column(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    (tmp_path / "runs.csv").write_text("id,note\na,missing\n")
    assert CSVBackend(config).list_runs() == []
    assert CSVBackend(config).list_runs(include_unattributed=True)[0].record_id is None
    assert CSVBackend(config).list_runs(record_id="child/child") == []


def test_run_query_reads_source_once(tmp_path, monkeypatch):
    project = Project(tmp_path)
    (tmp_path / "runs.csv").write_text("id,record_id\none,first\ntwo,second\n")
    reads = []
    original = CSVBackend._read_source_runs

    def read_source(self):
        reads.append(self)
        return original(self)

    monkeypatch.setattr(CSVBackend, "_read_source_runs", read_source)

    assert [run.id for run in CSVBackend(project).list_runs()] == ["one", "two"]
    assert len(reads) == 1
