from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.io import atomic_write
from hiraku.project import Project
from hiraku.record import Record, list_records, load_record


def test_paths_and_cross_root_reparenting(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    other = Record.create("other", project=config)
    middle = Record.create("middle", parent_id=first.id, project=config)
    leaf = Record.create("leaf", parent_id=middle.id, project=config)
    assert first.path == config.records_dir / "first/first.md"
    assert middle.path == config.records_dir / "first/middle.md"
    assert leaf.path == config.records_dir / "first/leaf.md"
    middle.reparent(other.id, project=config)
    assert (
        load_record("middle", project=config).path
        == config.records_dir / "other/middle.md"
    )
    assert (
        load_record("leaf", project=config).path == config.records_dir / "other/leaf.md"
    )
    assert [item.id for item in other.descendants(project=config)] == ["leaf", "middle"]
    middle.reparent(None, project=config)
    assert (
        load_record("middle", project=config).path
        == config.records_dir / "middle/middle.md"
    )
    assert (
        load_record("leaf", project=config).path
        == config.records_dir / "middle/leaf.md"
    )


def test_invalid_links_cycles_collisions_and_delete(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    second = Record.create("second", parent_id=first.id, project=config)
    third = Record.create("third", related_to=[second.id], project=config)
    with pytest.raises(ValidationError, match="ancestor"):
        first.reparent(second.id, project=config)
    with pytest.raises(RecordExistsError):
        Record.create("second", project=config)
    with pytest.raises(RecordNotFoundError):
        Record.create("fourth", parent_id="missing", project=config)
    second.related_to = ["missing"]
    with pytest.raises(RecordNotFoundError):
        second.save()
    second.related_to = []
    with pytest.raises(ValidationError, match="referenced"):
        second.delete(project=config)
    third.delete(project=config)
    second.delete(project=config)
    assert [item.id for item in list_records(project=config)] == ["first"]


def test_duplicate_record_id_without_reparent_journal_is_an_error(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("duplicate", project=config)
    atomic_write(
        config.records_dir / "other" / "duplicate.md",
        record.path.read_text(encoding="utf-8"),
    )

    with pytest.raises(ValidationError, match="Duplicate record ID"):
        list_records(project=config)


def test_nearest_definition_null_and_local_exceptions(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create(
        "root",
        project=config,
        goal="Improve",
        branch="work",
        status="accepted",
        decision="keep",
        type="task",
    )
    child = Record.create("child", parent_id=root.id, project=config, goal=None)
    resolved = child.resolve(project=config)
    assert resolved.values["goal"] is None
    assert resolved.sources["goal"] == "child"
    assert resolved.values["branch"] == "work"
    assert resolved.sources["branch"] == "root"
    assert "status" not in resolved.values
    assert "decision" not in resolved.values
    assert "type" not in resolved.values
    assert "command" not in resolved.values


def test_reparenting_root_with_other_files_requires_manual_move(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    target = Record.create("target", project=config)
    (source.path.parent / "artifact.bin").write_bytes(b"research output")
    with pytest.raises(ValidationError, match="Move files"):
        source.reparent(target.id, project=config)
    assert load_record("source", project=config).parent_id is None


def test_reparenting_retries_transient_old_file_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
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
    source.reparent(target.id, project=config)

    assert all(not path.exists() for path in old_paths.values())
    assert load_record("source", project=config).parent_id == target.id
    assert load_record("child", project=config).parent_id == source.id


def test_reparenting_journal_keeps_records_listable_during_persistent_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    old_paths = {source.id: source.path, child.id: child.path}
    original_unlink = Path.unlink

    def fail_on_child(path: Path, *args, **kwargs) -> None:
        if path == old_paths[child.id]:
            raise OSError("simulated unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_on_child)
    source.reparent(target.id, project=config)

    target_paths = {
        source.id: config.records_dir / "target" / "source.md",
        child.id: config.records_dir / "target" / "child.md",
    }
    assert all(path.exists() for path in target_paths.values())
    assert source.parent_id == target.id
    journal = json.loads(
        (config.hiraku_dir / ".reparent-transaction.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "committed"
    assert journal["old_root"] == "source"
    assert journal["new_root"] == "target"
    assert set(journal["affected_ids"]) == {"source", "child"}
    assert {record.id for record in list_records(project=config)} == {
        "source",
        "child",
        "target",
    }
    assert load_record("source", project=config).path == target_paths[source.id]
    assert load_record("child", project=config).path == target_paths[child.id]
    assert old_paths[child.id].exists()
    with pytest.raises(ValidationError, match="pending reparent recovery"):
        load_record("child", project=config).delete(project=config)

    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert {record.id for record in list_records(project=config)} == {
        "source",
        "child",
        "target",
    }
    assert all(not path.exists() for path in old_paths.values())
    assert not (config.hiraku_dir / ".reparent-transaction.json").exists()


def test_reparenting_cleans_incomplete_destination_copies_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    old_paths = {source.id: source.path, child.id: child.path}
    destination_child = config.records_dir / "target" / "child.md"
    original_atomic_write = atomic_write
    failed = False

    def fail_after_copy(path: Path, content: str, **options) -> None:
        nonlocal failed
        original_atomic_write(path, content, **options)
        if path == destination_child and not failed:
            failed = True
            raise OSError("simulated write failure")

    monkeypatch.setattr("hiraku.record.atomic_write", fail_after_copy)
    with pytest.raises(OSError, match="simulated write failure"):
        source.reparent(target.id, project=config)

    assert all(path.exists() for path in old_paths.values())
    assert not (config.records_dir / "target" / "source.md").exists()
    assert not destination_child.exists()
    assert source.parent_id is None
    assert not (config.hiraku_dir / ".reparent-transaction.json").exists()


@pytest.mark.parametrize("failure_after_replace", [False, True])
def test_reparenting_resolves_commit_journal_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_after_replace: bool,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    original_atomic_write = atomic_write
    journal_path = config.hiraku_dir / ".reparent-transaction.json"

    def fail_during_commit(path: Path, content: str, **options) -> None:
        is_commit = path == journal_path and json.loads(content)["phase"] == "committed"
        if is_commit and not failure_after_replace:
            raise OSError("simulated failure before journal commit")
        original_atomic_write(path, content, **options)
        if is_commit:
            raise OSError("simulated failure after journal commit")

    monkeypatch.setattr("hiraku.record.atomic_write", fail_during_commit)
    if failure_after_replace:
        source.reparent(target.id, project=config)
        assert load_record("source", project=config).parent_id == target.id
        assert load_record("child", project=config).parent_id == source.id
        assert not journal_path.exists()
    else:
        with pytest.raises(OSError, match="before journal commit"):
            source.reparent(target.id, project=config)
        assert load_record("source", project=config).parent_id is None
        assert load_record("child", project=config).path == child.path
        assert not (config.records_dir / "target" / "source.md").exists()
        assert not (config.records_dir / "target" / "child.md").exists()
        assert not journal_path.exists()


def test_preparing_journal_keeps_old_copies_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config, goal="old value")
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    old_child = child.path
    destination_child = config.records_dir / "target" / "child.md"
    original_unlink = Path.unlink
    original_atomic_write = atomic_write
    failed = False

    def fail_after_destination_copy(path: Path, content: str, **options) -> None:
        nonlocal failed
        original_atomic_write(path, content, **options)
        if path == destination_child and not failed:
            failed = True
            raise OSError("simulated destination write failure")

    def fail_destination_cleanup(path: Path, *args, **kwargs) -> None:
        if path == destination_child:
            raise OSError("simulated destination cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr("hiraku.record.atomic_write", fail_after_destination_copy)
    monkeypatch.setattr(Path, "unlink", fail_destination_cleanup)
    with pytest.raises(ExceptionGroup, match="recovery is pending"):
        source.reparent(target.id, project=config)

    journal = json.loads(
        (config.hiraku_dir / ".reparent-transaction.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "preparing"
    assert destination_child.exists()
    assert load_record("source", project=config).path == source.path
    assert load_record("child", project=config).path == old_child
    resolved = load_record("source", project=config).resolve(project=config)
    assert resolved.values["goal"] == "old value"

    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert {record.id for record in list_records(project=config)} == {
        "source",
        "child",
        "target",
    }
    assert not destination_child.exists()
    assert not (config.hiraku_dir / ".reparent-transaction.json").exists()


def test_preparing_journal_errors_when_old_authoritative_copy_is_missing(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    Record.create("target", project=config)
    ids = [source.id, child.id]
    moves = [
        {
            "old": f"source/{record_id}.md",
            "new": f"target/{record_id}.md",
        }
        for record_id in ids
    ]
    for old_record, move in zip((source, child), moves, strict=True):
        atomic_write(
            config.records_dir / move["new"],
            old_record.path.read_text(encoding="utf-8"),
        )
    journal_path = config.hiraku_dir / ".reparent-transaction.json"
    atomic_write(
        journal_path,
        json.dumps(
            {
                "phase": "preparing",
                "old_root": "source",
                "new_root": "target",
                "affected_ids": ids,
                "moves": moves,
            }
        ),
    )
    child.path.unlink()

    with pytest.raises(
        ValidationError, match="Authoritative pre-commit copy is missing"
    ):
        list_records(project=config)

    assert all((config.records_dir / move["new"]).exists() for move in moves)
    assert journal_path.exists()


def test_committed_journal_errors_when_new_authoritative_copy_is_missing(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    Record.create("target", project=config)
    ids = [source.id, child.id]
    moves = [
        {
            "old": f"source/{record_id}.md",
            "new": f"target/{record_id}.md",
        }
        for record_id in ids
    ]
    atomic_write(
        config.records_dir / moves[0]["new"],
        source.path.read_text(encoding="utf-8"),
    )
    journal_path = config.hiraku_dir / ".reparent-transaction.json"
    atomic_write(
        journal_path,
        json.dumps(
            {
                "phase": "committed",
                "old_root": "source",
                "new_root": "target",
                "affected_ids": ids,
                "moves": moves,
            }
        ),
    )

    with pytest.raises(
        ValidationError, match="Authoritative committed copy is missing"
    ):
        list_records(project=config)

    assert source.path.exists()
    assert child.path.exists()
    assert (config.records_dir / moves[0]["new"]).exists()
    assert journal_path.exists()
