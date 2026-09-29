from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.record import Record, list_records, load_record
from hiraku.tracking.csv import CSVBackend


def test_paths_and_cross_root_reparenting(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    first = Record.create("first", config=config)
    other = Record.create("other", config=config)
    middle = Record.create("middle", parent_id=first.id, config=config)
    leaf = Record.create("leaf", parent_id=middle.id, config=config)
    assert first.path == config.records_dir / "first/first.md"
    assert middle.path == config.records_dir / "first/middle.md"
    assert leaf.path == config.records_dir / "first/leaf.md"
    middle.reparent(other.id, config=config)
    assert (
        load_record("middle", config=config).path
        == config.records_dir / "other/middle.md"
    )
    assert (
        load_record("leaf", config=config).path == config.records_dir / "other/leaf.md"
    )
    assert [item.id for item in other.descendants(config=config)] == ["leaf", "middle"]
    middle.reparent(None, config=config)
    assert (
        load_record("middle", config=config).path
        == config.records_dir / "middle/middle.md"
    )
    assert (
        load_record("leaf", config=config).path == config.records_dir / "middle/leaf.md"
    )


def test_invalid_links_cycles_collisions_and_delete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    first = Record.create("first", config=config)
    second = Record.create("second", parent_id=first.id, config=config)
    third = Record.create("third", related_to=[second.id], config=config)
    with pytest.raises(ValidationError, match="ancestor"):
        first.reparent(second.id, config=config)
    with pytest.raises(RecordExistsError):
        Record.create("second", config=config)
    with pytest.raises(RecordNotFoundError):
        Record.create("fourth", parent_id="missing", config=config)
    second.related_to = ["missing"]
    with pytest.raises(RecordNotFoundError):
        second.save()
    second.related_to = []
    with pytest.raises(ValidationError, match="referenced"):
        second.delete(config=config)
    third.delete(config=config)
    second.delete(config=config)
    assert [item.id for item in list_records(config=config)] == ["first"]


def test_nearest_definition_null_and_local_exceptions(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create(
        "root",
        config=config,
        goal="Improve",
        branch="work",
        status="accepted",
        decision="keep",
        type="task",
        entrypoints={"train": {"command": ["echo", "root"]}},
    )
    child = Record.create("child", parent_id=root.id, config=config, goal=None)
    resolved = child.resolve(config=config)
    assert resolved.values["goal"] is None
    assert resolved.sources["goal"] == "child"
    assert resolved.values["branch"] == "work"
    assert resolved.sources["branch"] == "root"
    assert "status" not in resolved.values
    assert "decision" not in resolved.values
    assert "type" not in resolved.values
    assert resolved.sources["entrypoints"] == {"train": "root"}


def test_direct_and_descendant_csv_runs(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create("root", config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    with (tmp_path / "runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "record_id", "metric.loss"])
        writer.writeheader()
        writer.writerow({"id": "one", "record_id": "root", "metric.loss": 0.5})
        writer.writerow({"id": "two", "record_id": "child", "metric.loss": 0.3})
    assert {run.id for run in root.runs(config=config)} == {"one", "two"}
    assert {run.id for run in root.runs(config=config, include_descendants=True)} == {
        "one",
        "two",
    }
    assert {run.id for run in child.runs(config=config)} == {"one", "two"}


def test_default_run_list_returns_attributed_runs_from_selected_source(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create("root", config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,outside\nfour,\n",
        encoding="utf-8",
    )

    assert {run.id for run in root.runs(config=config)} == {"one", "two", "three"}
    assert {run.id for run in child.runs(config=config)} == {"one", "two", "three"}
    assert {
        run.id
        for run in root.runs(
            config=config, include_descendants=True, include_unattributed=True
        )
    } == {"one", "two", "four"}
    assert {run.id for run in root.runs(config=config, include_descendants=True)} == {
        "one",
        "two",
    }


def test_subtree_run_query_scans_shared_csv_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    root = Record.create("root", config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    Record.create("leaf", parent_id=child.id, config=config)
    original = CSVBackend._read_source_runs
    calls = 0

    def count_reads(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(CSVBackend, "_read_source_runs", count_reads)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,leaf\n", encoding="utf-8"
    )

    assert {run.id for run in root.runs(config=config, include_descendants=True)} == {
        "one",
        "two",
        "three",
    }
    assert calls == 1


def test_reparenting_root_with_other_files_requires_manual_move(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    source = Record.create("source", config=config)
    target = Record.create("target", config=config)
    (source.path.parent / "artifact.bin").write_bytes(b"research output")
    with pytest.raises(ValidationError, match="Move files"):
        source.reparent(target.id, config=config)
    assert load_record("source", config=config).parent_id is None


def test_reparenting_rolls_back_if_old_file_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    source = Record.create("source", config=config)
    child = Record.create("child", parent_id=source.id, config=config)
    target = Record.create("target", config=config)
    old_paths = {source.id: source.path, child.id: child.path}
    original_unlink = Path.unlink
    failed = False

    def fail_on_child(path: Path, *args, **kwargs) -> None:
        nonlocal failed
        if path == old_paths[child.id] and not failed:
            failed = True
            raise OSError("simulated unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_on_child)
    with pytest.raises(OSError, match="simulated unlink failure"):
        source.reparent(target.id, config=config)

    assert all(path.exists() for path in old_paths.values())
    assert not (config.records_dir / "target" / "source.md").exists()
    assert not (config.records_dir / "target" / "child.md").exists()
    assert load_record("source", config=config).parent_id is None
