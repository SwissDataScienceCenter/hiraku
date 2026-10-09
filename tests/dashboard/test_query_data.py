from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fixtures import create_acceptance_project

import hiraku.dashboard.data as data
import hiraku.record as record_module
from hiraku.config import HirakuConfig
from hiraku.errors import BackendError, RecordNotFoundError, ValidationError
from hiraku.record import Record
from hiraku.run import Run


def _files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run(
    run_id: str,
    record_id: str,
    *,
    source: str = "csv:runs.csv",
    metrics: dict[str, object] | None = None,
    timestamp: datetime | None = None,
) -> data.RunView:
    return data.RunView(
        key=f"{source}::{run_id}",
        id=run_id,
        record_id=record_id,
        timestamp=timestamp,
        commit=None,
        entrypoint=None,
        url=None,
        metrics=dict(metrics or {}),
        params={},
        source=source,
    )


def test_snapshot_resolves_inherited_backend_without_mutation(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "baseline",
        config=config,
        activate=False,
        type="investigation",
        tracking_backend={"type": "csv", "path": "results/baseline.csv"},
    )
    Record.create(
        "child",
        parent_id="baseline",
        config=config,
        activate=False,
        status="running",
    )
    runs_path = tmp_path / "results" / "baseline.csv"
    runs_path.parent.mkdir()
    runs_path.write_text("id,record_id\nrun-1,child\n", encoding="utf-8")
    before = _files(tmp_path)

    snapshot = data.build_snapshot(config, record_id="child")

    assert _files(tmp_path) == before
    child = next(view for view in snapshot.records if view.id == "child")
    assert child.fields["tracking_backend"] == {
        "type": "csv",
        "path": "results/baseline.csv",
    }
    assert child.sources["tracking_backend"] == "baseline"
    assert child.fields.get("type") is None  # `type` is local and not inherited
    assert child.fields["status"] == "running"
    assert child.sources["status"] == "child"
    assert [run.id for run in snapshot.runs] == ["run-1"]
    assert snapshot.source is not None and snapshot.source.record_id == "child"


def test_record_and_source_scope_are_distinct(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("a", config=config, activate=False)
    Record.create("b", config=config, activate=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id\nrun-a,a\nrun-b,b\nunattributed,\n", encoding="utf-8"
    )

    record_scope = data.build_snapshot(config, record_id="a", scope="record")
    source_scope = data.build_snapshot(config, record_id="a", scope="source")

    assert [run.id for run in record_scope.runs] == ["run-a"]
    assert {run.id for run in source_scope.runs} == {"run-a", "run-b"}
    assert record_scope.source is not None and record_scope.source.scope == "record"
    assert source_scope.source is not None and source_scope.source.scope == "source"


def test_two_sources_with_identical_run_ids_get_distinct_keys(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "first",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "first.csv"},
    )
    Record.create(
        "second",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "second.csv"},
    )
    (tmp_path / "first.csv").write_text("id,record_id\nrun-1,first\n", encoding="utf-8")
    (tmp_path / "second.csv").write_text(
        "id,record_id\nrun-1,second\n", encoding="utf-8"
    )

    first = data.build_snapshot(config, record_id="first")
    second = data.build_snapshot(config, record_id="second")

    assert first.runs[0].id == second.runs[0].id == "run-1"
    assert first.runs[0].key != second.runs[0].key
    assert first.runs[0].source != second.runs[0].source


def test_missing_metrics_and_parent_are_reported_not_repaired(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("parent", config=config, activate=False)
    orphan = config.records_dir / "parent" / "orphan.md"
    orphan.write_text(
        "---\nid: orphan\nparent_id: ghost\n---\nNo parent here.\n", encoding="utf-8"
    )
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc\nwith-metric,parent,0.9\nmissing-metric,parent,\n",
        encoding="utf-8",
    )

    snapshot = data.build_snapshot(config, record_id="parent")

    assert {view.id for view in snapshot.records} == {"parent", "orphan"}
    assert any(error.kind == "record" for error in snapshot.errors)
    metrics = {run.id: run.metrics for run in snapshot.runs}
    assert metrics["with-metric"]["acc"] == "0.9"
    assert "acc" not in metrics["missing-metric"]
    assert data.sort_runs(snapshot.runs, "acc")[-1].id == "missing-metric"


def test_malformed_record_does_not_abort_snapshot(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("valid", config=config, activate=False)
    bad = config.records_dir / "valid" / "bad.md"
    bad.write_text("---\nid: [unclosed\n---\n", encoding="utf-8")

    snapshot = data.build_snapshot(config)

    assert any("bad.md" in error.message for error in snapshot.errors)
    assert "valid" in {view.id for view in snapshot.records}


@pytest.mark.parametrize("phase", ["preparing", "committed"])
def test_pending_reparent_journal_is_detected_not_repaired(
    tmp_path: Path, phase: str
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    journal = config.hiraku_dir / ".reparent-transaction.json"
    journal.write_text(
        f'{{"phase": "{phase}", "moves": [], "old_root": "root", '
        '"new_root": "root", "affected_ids": ["root"]}',
        encoding="utf-8",
    )
    before = journal.read_bytes()

    snapshot = data.build_snapshot(config)

    assert any(error.kind == "journal" for error in snapshot.errors)
    assert journal.read_bytes() == before
    assert "root" in {view.id for view in snapshot.records}


def test_concurrent_replacement_reads_once_then_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    original = data.Record.load
    calls = 0

    def flaky_load(path: Path) -> Record:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("file replaced during read")
        return original(path)

    monkeypatch.setattr(data.Record, "load", staticmethod(flaky_load))

    snapshot = data.build_snapshot(config)

    assert calls == 2
    assert "root" in {view.id for view in snapshot.records}
    assert not any(error.kind == "transient" for error in snapshot.errors)

    def always_missing(path: Path) -> Record:
        raise OSError("file replaced during read")

    monkeypatch.setattr(data.Record, "load", staticmethod(always_missing))
    failing = data.build_snapshot(config)
    assert any(error.kind == "transient" for error in failing.errors)


def test_adapter_is_called_once_and_recovery_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard must not invoke record recovery")

    monkeypatch.setattr(record_module, "_recover_reparenting", fail_if_called)
    monkeypatch.setattr(record_module, "_record_index", fail_if_called)

    class FakeBackend:
        backend_name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def list_runs(self, *, record_id: str) -> list[Run]:
            self.calls += 1
            return [Run(id="run-1", record_id=record_id)]

        def list_all_recorded_runs(self) -> list[Run]:
            self.calls += 1
            return [Run(id="run-1", record_id="root")]

    backend = FakeBackend()
    monkeypatch.setattr(data, "backend_for", lambda spec, cfg: backend)

    snapshot = data.build_snapshot(config, record_id="root")

    assert backend.calls == 1
    assert [run.id for run in snapshot.runs] == ["run-1"]
    assert snapshot.errors == ()


def test_unavailable_backend_is_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)

    def explode(spec: object, cfg: object) -> None:
        raise BackendError("backend exploded")

    monkeypatch.setattr(data, "backend_for", explode)

    snapshot = data.build_snapshot(config, record_id="root")

    assert snapshot.runs == ()
    assert any(error.kind == "backend" for error in snapshot.errors)


def test_invalid_record_id_raises_record_not_found(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(RecordNotFoundError):
        data.build_snapshot(config, record_id="missing")


def test_sort_runs_handles_numeric_negative_and_missing_values() -> None:
    runs = [
        _run("a", "r", metrics={"score": "0.5"}),
        _run("b", "r", metrics={"score": "-1.2"}),
        _run("c", "r", metrics={}),
        _run("d", "r", metrics={"score": "2.0"}),
    ]

    ascending = data.sort_runs(runs, "score")
    descending = data.sort_runs(runs, "score", ascending=False)

    assert [run.id for run in ascending] == ["b", "a", "d", "c"]
    assert [run.id for run in descending] == ["d", "a", "b", "c"]
    assert data.sort_runs([], "score") == []


def test_sort_runs_timestamp_puts_missing_last() -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 1, 2, tzinfo=UTC)
    runs = [
        _run("missing", "r", timestamp=None),
        _run("late", "r", timestamp=late),
        _run("early", "r", timestamp=early),
    ]

    assert [run.id for run in data.sort_runs(runs, "timestamp")] == [
        "early",
        "late",
        "missing",
    ]
    assert [run.id for run in data.sort_runs(runs, "timestamp", ascending=False)] == [
        "late",
        "early",
        "missing",
    ]


def test_subtree_scope_includes_descendants_and_excludes_unrelated(
    tmp_path: Path,
) -> None:
    config = create_acceptance_project(tmp_path)

    snapshot = data.build_snapshot(config, record_id="baseline", scope="subtree")

    assert snapshot.version == 2
    assert [run.id for run in snapshot.runs].count("a-1") == 2
    assert {run.id for run in snapshot.runs} == {"base-1", "a-1", "a-missing"}
    assert "u-1" not in {run.id for run in snapshot.runs}
    assert {run.source for run in snapshot.runs if run.id == "a-1"} == {
        f"csv:{(tmp_path / 'results' / 'a.csv').resolve()}",
        f"csv:{(tmp_path / 'results' / 'b.csv').resolve()}",
    }
    assert len(snapshot.sources) == 3
    assert {source.record_id for source in snapshot.sources} == {
        "baseline",
        "child-a",
        "child-b",
    }
    assert all(source.run_count is not None for source in snapshot.sources)


def test_subtree_leaf_and_empty_subtree_are_complete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("parent", config=config, activate=False)
    Record.create(
        "leaf",
        parent_id="parent",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "leaf.csv"},
    )
    (tmp_path / "leaf.csv").write_text("id,record_id\nleaf-1,leaf\n", encoding="utf-8")

    snapshot = data.build_snapshot(config, record_id="leaf", scope="subtree")

    assert [run.id for run in snapshot.runs] == ["leaf-1"]
    assert len(snapshot.sources) == 1
    assert snapshot.sources[0].run_count == 1
    assert snapshot.sources[0].status == "complete"


def test_subtree_invalid_scope_is_rejected(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    with pytest.raises(ValidationError, match="subtree"):
        data.build_snapshot(config, record_id="missing", scope="subtreee")


def test_subtree_cycle_terminates_without_hanging(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    folder = config.records_dir / "root"
    folder.mkdir()
    (folder / "a.md").write_text("---\nid: a\nparent_id: b\n---\n")
    (folder / "b.md").write_text("---\nid: b\nparent_id: a\n---\n")
    (tmp_path / "runs.csv").write_text("id,record_id\na-1,a\nb-1,b\n", encoding="utf-8")

    snapshot = data.build_snapshot(config, record_id="a", scope="subtree")

    assert {run.id for run in snapshot.runs} == {"a-1", "b-1"}
    assert any(error.kind == "record" for error in snapshot.errors)


def test_subtree_partial_failure_keeps_successful_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = create_acceptance_project(tmp_path)
    original = data._list_source_runs

    def fail_one(backend, *, record_id, options):
        if getattr(backend, "path", None) is not None and str(backend.path).endswith(
            "a.csv"
        ):
            raise BackendError("a.csv exploded")
        return original(backend, record_id=record_id, options=options)

    monkeypatch.setattr(data, "_list_source_runs", fail_one)

    snapshot = data.build_snapshot(config, record_id="baseline", scope="subtree")

    assert {run.id for run in snapshot.runs} == {"base-1", "a-1"}
    assert any(error.kind == "backend" for error in snapshot.errors)
    assert len(snapshot.sources) == 2
    assert "a.csv" not in {source.label for source in snapshot.sources}
