from __future__ import annotations

from dataclasses import replace

import pytest
from fixtures import create_acceptance_project

import hiraku.dashboard.data as data
from hiraku.config import HirakuConfig
from hiraku.dashboard.app import app
from hiraku.dashboard.plots import metric_direction
from hiraku.dashboard.session import DashboardSession
from hiraku.dashboard.views import sorted_runs
from hiraku.record import Record
from hiraku.tracking.mlflow import MLflowBackend


@pytest.mark.parametrize("record_id", ["child-a", "unrelated", None])
def test_notebook_executes_reactive_cells(tmp_path, monkeypatch, record_id):
    config = create_acceptance_project(tmp_path)
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(config.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", record_id or "")
    outputs, definitions = app.run()
    assert len(outputs) >= 10
    assert definitions["snapshot"].selected_record_id == record_id
    assert definitions["record_select"] is not None
    assert definitions["runs_table"] is not None
    if record_id:
        assert definitions["plot"] is not None


def test_empty_notebook_keeps_record_browser(tmp_path, monkeypatch):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(config.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "")
    _, definitions = app.run()
    assert definitions["snapshot"].records == ()
    assert definitions["record_select"] is not None


def test_selection_and_scope_share_one_source_fetch(tmp_path, monkeypatch):
    config = create_acceptance_project(tmp_path)
    original = data._list_source_runs
    calls = []

    def count(backend, **kwargs):
        calls.append(backend)
        return original(backend, **kwargs)

    monkeypatch.setattr(data, "_list_source_runs", count)
    session = DashboardSession(config)
    session.reload_records()
    first = session.refresh("child-a", cached=True).snapshot
    second = session.refresh("child-a", "source", cached=True).snapshot
    assert len(calls) == 1
    assert first.refreshed_at == second.refreshed_at
    session.reload_records()
    session.refresh("child-a", cached=True)
    assert len(calls) == 2


def test_backend_failure_retains_last_successful_fetch(tmp_path, monkeypatch):
    config = create_acceptance_project(tmp_path)
    session = DashboardSession(config)
    first = session.refresh("child-a").snapshot

    def fail(*args, **kwargs):
        raise RuntimeError("credentials must not appear in the snapshot")

    monkeypatch.setattr(data, "_list_source_runs", fail)
    failed = session.refresh("child-a")
    assert failed.snapshot.stale
    assert failed.snapshot.runs == first.runs
    assert failed.snapshot.refreshed_at == first.refreshed_at
    assert "credentials must not" not in failed.error
    other = session.refresh("child-b")
    assert other.snapshot.selected_record_id == "child-b"
    assert other.snapshot.runs == ()


def test_missing_mlflow_database_is_never_created(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create("root", config=config, activate=False, tracking_backend="mlflow")
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    snapshot = data.build_snapshot(config, record_id="root")
    assert any(error.kind == "backend" for error in snapshot.errors)
    assert not (tmp_path / "mlflow.db").exists()
    assert before == {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


def test_mlflow_source_identity_includes_storage_without_credentials(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    first = MLflowBackend(config, tracking_uri="https://user:secret@first.example")
    second = MLflowBackend(config, tracking_uri="https://user:secret@second.example")
    a = data._source_info(first, config, "root", "record")
    b = data._source_info(second, config, "root", "record")
    assert a.identity != b.identity
    assert "secret" not in repr(a)


def test_namespaced_sorting_and_missing_nonfinite_values(tmp_path):
    config = create_acceptance_project(tmp_path)
    snapshot = data.build_snapshot(config, record_id="child-a")
    base = snapshot.runs[0]
    runs = (
        replace(
            base,
            key="a",
            id="a",
            record_id="z",
            metrics={"id": 10, "score": 1},
            params={"score": 30},
        ),
        replace(
            base,
            key="b",
            id="b",
            record_id="a",
            metrics={"id": 2, "score": 20},
            params={"score": 2},
        ),
        replace(
            base,
            key="c",
            id="c",
            record_id=None,
            metrics={"id": float("nan")},
            params={},
        ),
    )
    snapshot = replace(snapshot, runs=runs)
    for field in ["record", "metric.id", "param.score"]:
        assert [r.id for r in sorted_runs(snapshot, field)] == ["b", "a", "c"]
    assert [r.id for r in sorted_runs(snapshot, "metric.score")] == ["a", "b", "c"]


def test_atomic_replacement_triggers_bounded_reread(tmp_path, monkeypatch):
    config = create_acceptance_project(tmp_path)
    original = Record.load
    calls = 0

    def replace_during_read(path):
        nonlocal calls
        calls += 1
        record = original(path)
        if calls == 1:
            replacement = path.with_suffix(".replacement")
            replacement.write_text(path.read_text() + "\nUpdated description.\n")
            replacement.replace(path)
        return record

    monkeypatch.setattr(Record, "load", replace_during_read)
    snapshot = data.build_snapshot(config)
    assert calls == 8
    assert any("Updated description." in r.markdown for r in snapshot.records)
    assert not any(e.kind == "transient" for e in snapshot.errors)


def test_deep_inheritance_does_not_hit_recursion_limit(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    folder = config.records_dir / "root"
    folder.mkdir()
    for i in range(1100):
        parent = f"parent_id: r{i - 1}\n" if i else "status: done\n"
        (folder / f"r{i}.md").write_text(f"---\nid: r{i}\n{parent}---\n")
    snapshot = data.build_snapshot(config, use_active_record=False)
    assert len(snapshot.records) == 1100
    assert not snapshot.errors


def test_source_metric_directions_require_consistent_declarations(tmp_path):
    config = create_acceptance_project(tmp_path)
    snapshot = data.build_snapshot(config, record_id="child-a", scope="source")
    assert metric_direction(snapshot, "acc") == "maximize"
    other = replace(snapshot.runs[0], key="other", record_id="child-b")
    records = tuple(
        replace(r, metrics=[{"name": "acc", "direction": "minimize"}])
        if r.id == "child-b"
        else r
        for r in snapshot.records
    )
    conflicting = replace(snapshot, records=records, runs=(*snapshot.runs, other))
    assert metric_direction(conflicting, "acc") is None
    missing = replace(other, record_id="missing-record")
    assert metric_direction(replace(snapshot, runs=(missing,)), "acc") is None
