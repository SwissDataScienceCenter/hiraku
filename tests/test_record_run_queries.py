from __future__ import annotations

import csv
from pathlib import Path

from hiraku.project import Project
from hiraku.record import Record
from hiraku.tracking.csv import CSVBackend


def test_runs_list_attributed_runs_from_current_backend(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    with (tmp_path / "runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "record_id", "metric.loss"])
        writer.writeheader()
        writer.writerow({"id": "one", "record_id": "root", "metric.loss": 0.5})
        writer.writerow({"id": "two", "record_id": "child", "metric.loss": 0.3})
    assert {run.id for run in root.runs(project=config)} == {"one"}
    assert {run.id for run in child.runs(project=config)} == {"two"}
    backend = child.resolve_tracking_backend(project=config)
    assert {run.id for run in backend.list_runs()} == {"one", "two"}


def test_default_run_list_returns_attributed_runs_from_selected_source(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,outside\nfour,\n",
        encoding="utf-8",
    )

    backend = child.resolve_tracking_backend(project=config)
    assert {run.id for run in backend.list_runs()} == {"one", "two", "three"}


def test_run_query_scans_selected_csv_once(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    Record.create("leaf", parent_id=child.id, project=config)
    original = CSVBackend._read_source_runs
    calls = 0

    def count_reads(self: CSVBackend):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(CSVBackend, "_read_source_runs", count_reads)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,leaf\n", encoding="utf-8"
    )

    assert {
        run.id for run in root.resolve_tracking_backend(project=config).list_runs()
    } == {
        "one",
        "two",
        "three",
    }
    assert calls == 1


def test_resolve_tracking_backend_uses_project_setting(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.tracking_backend = {"type": "csv", "path": "results/root.csv"}
    root = Record.create("root", project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    other = Record.create("other", project=config)

    for record in (root, child, other):
        backend = record.resolve_tracking_backend(project=config)
        assert isinstance(backend, CSVBackend)
        assert backend.path == (tmp_path / "results" / "root.csv").resolve()
