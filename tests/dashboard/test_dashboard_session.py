from __future__ import annotations

import threading
from pathlib import Path

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
