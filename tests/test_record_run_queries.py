from __future__ import annotations

import csv
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace
from hiraku.tracking.csv import CSVBackend


def test_runs_list_attributed_runs_from_current_backend(tmp_path: Path) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    root = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(root)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=root.id)
    config.set_active_record(child)
    with (tmp_path / "runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "record_id", "metric.loss"])
        writer.writeheader()
        writer.writerow({"id": "one", "record_id": "root", "metric.loss": 0.5})
        writer.writerow({"id": "two", "record_id": "child", "metric.loss": 0.3})
    assert {run.id for run in root.runs(config=config)} == {"one"}
    assert {run.id for run in child.runs(config=config)} == {"two"}
    backend = child.resolve_tracking_backend(config=config)
    assert {run.id for run in backend.list_runs()} == {"one", "two"}


def test_default_run_list_returns_attributed_runs_from_selected_source(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    root = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(root)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=root.id)
    config.set_active_record(child)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,outside\nfour,\n",
        encoding="utf-8",
    )

    backend = child.resolve_tracking_backend(config=config)
    assert {run.id for run in backend.list_runs()} == {"one", "two", "three"}


def test_run_query_scans_selected_csv_once(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    root = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(root)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=root.id)
    config.set_active_record(child)
    _created_record = Record.create(
        workspace, "leaf/leaf.md", id="leaf", parent_id=child.id
    )
    config.set_active_record(_created_record)
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
        run.id for run in root.resolve_tracking_backend(config=config).list_runs()
    } == {
        "one",
        "two",
        "three",
    }
    assert calls == 1


def test_resolve_tracking_backend_uses_project_setting(tmp_path: Path) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    config.tracking_backend = {"type": "csv", "path": "results/root.csv"}
    workspace = open_workspace(config)
    root = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(root)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=root.id)
    config.set_active_record(child)
    other = Record.create(workspace, "other/other.md", id="other")
    config.set_active_record(other)

    for record in (root, child, other):
        backend = record.resolve_tracking_backend(config=config)
        assert isinstance(backend, CSVBackend)
        assert backend.path == (tmp_path / "results" / "root.csv").resolve()
