from __future__ import annotations

import threading
from pathlib import Path

import hiraku.dashboard.data as data
import hiraku.dashboard.session as session_module
from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.data import RunView
from hiraku.dashboard.plots import pareto_points, progress_points
from hiraku.dashboard.session import DashboardSession
from hiraku.dashboard.views import sorted_runs
from hiraku.record import Record


def test_refresh_coalesces_overlapping_requests(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    session = DashboardSession(config)
    first = session.refresh("root")
    assert first.snapshot is not None and not first.snapshot.stale

    original = session_module.build_snapshot
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(session_module, "build_snapshot", blocking)

    slow_result: list = []
    thread = threading.Thread(
        target=lambda: slow_result.append(session.refresh("root"))
    )
    thread.start()
    assert entered.wait(timeout=5)
    coalesced = session.refresh("root")
    release.set()
    thread.join(timeout=5)

    assert calls == 1
    assert coalesced.coalesced is True
    assert coalesced.snapshot is not None and coalesced.snapshot.stale
    assert "already in flight" in (coalesced.error or "")
    assert slow_result[0].snapshot is not None


def test_refresh_falls_back_to_last_successful_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    session = DashboardSession(config)
    first = session.refresh("root")
    assert first.snapshot is not None

    def explode(*args, **kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(session_module, "build_snapshot", explode)
    result = session.refresh("root")

    assert result.snapshot is not None and result.snapshot.stale
    assert "backend exploded" in (result.error or "")
    assert result.coalesced is False


def test_refresh_without_last_snapshot_returns_error(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(session_module, "build_snapshot", explode)
    session = DashboardSession(config)
    result = session.refresh("root")

    assert result.snapshot is None
    assert result.error == "boom"


def test_visual_helpers_never_query_the_tracker(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,timestamp,metric.acc,metric.loss\n"
        "a,root,2026-01-01T00:00:00Z,0.9,0.1\n"
        "b,root,2026-01-02T00:00:00Z,0.8,0.2\n",
        encoding="utf-8",
    )
    snapshot = build_snapshot(config, record_id="root")

    def fail_if_queried(*args, **kwargs):
        raise AssertionError("visual helpers must not query the tracker")

    monkeypatch.setattr("hiraku.dashboard.data._list_source_runs", fail_if_queried)

    assert sorted_runs(snapshot, "timestamp")
    assert progress_points(snapshot.runs, "acc").frame is not None
    assert pareto_points(snapshot.runs, "acc", "loss").frame is not None


def test_run_view_helpers_do_not_mutate_snapshot(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    snapshot = build_snapshot(config, record_id="root")
    runs_before = list(snapshot.runs)

    sorted_runs(snapshot, "id")
    progress_points(snapshot.runs, "missing")

    assert list(snapshot.runs) == runs_before
    assert isinstance(snapshot.runs[0], RunView) if snapshot.runs else True


def test_subtree_shared_source_is_queried_once_per_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "baseline",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/shared.csv"},
    )
    Record.create("child-a", parent_id="baseline", config=config, activate=False)
    Record.create("child-b", parent_id="baseline", config=config, activate=False)
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "shared.csv").write_text(
        "id,record_id\nbase-1,baseline\na-1,child-a\nb-1,child-b\nu-1,unrelated\n",
        encoding="utf-8",
    )
    session = DashboardSession(config)
    session.reload_records()
    original = data._list_source_runs
    calls = []

    def counting(backend, *, record_id, options):
        calls.append(1)
        return original(backend, record_id=record_id, options=options)

    monkeypatch.setattr(data, "_list_source_runs", counting)

    snapshot = session.refresh("baseline", "subtree", cached=True).snapshot

    assert snapshot is not None
    assert len(calls) == 1
    assert {run.id for run in snapshot.runs} == {"base-1", "a-1", "b-1"}
    assert len(snapshot.sources) == 1
    assert snapshot.sources[0].run_count == 3


def test_subtree_partial_snapshot_is_not_replaced_with_stale(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "parent",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/a.csv"},
    )
    Record.create(
        "child",
        parent_id="parent",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/b.csv"},
    )
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "a.csv").write_text(
        "id,record_id\np-1,parent\n", encoding="utf-8"
    )
    (tmp_path / "results" / "b.csv").write_text(
        "id,record_id\nc-1,child\n", encoding="utf-8"
    )
    session = DashboardSession(config)
    first = session.refresh("parent", "subtree")
    assert first.snapshot is not None and not first.snapshot.stale
    assert {run.id for run in first.snapshot.runs} == {"p-1", "c-1"}

    original = data._list_source_runs

    def fail_a(backend, *, record_id, options):
        path = getattr(backend, "path", None)
        if path is not None and str(path).endswith("a.csv"):
            raise RuntimeError("a.csv exploded")
        return original(backend, record_id=record_id, options=options)

    monkeypatch.setattr(data, "_list_source_runs", fail_a)

    result = session.refresh("parent", "subtree")

    assert result.snapshot is not None
    assert not result.snapshot.stale
    assert {run.id for run in result.snapshot.runs} == {"c-1"}
    assert "Tracking source unavailable" in (result.error or "")
