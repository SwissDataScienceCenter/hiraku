from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.io import atomic_write
from hiraku.project import Project
from hiraku.record import Record, list_records, load_record, validate_identifier
from hiraku.tracking.csv import CSVBackend


def test_markdown_round_trip_order_conflicts_and_identity(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    task = Record(
        id="sample", markdown="# Body\n\nVerbatim.\n", zeta=1, alpha={"x": True}
    )
    path = task.save(config.records_dir / "sample/sample.md")
    loaded = Record.load(path)

    assert list(loaded.to_dict()) == ["id", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    loaded.save()
    assert Record.load(path).to_dict()["new"] == [1, 2]
    assert load_record("sample", project=config).id == "sample"
    assert [record.id for record in list_records(project=config)] == ["sample"]
    assert [record.id for record in Record.list(config.records_dir.glob("*/*.md"))] == [
        "sample"
    ]
    with pytest.raises(ValidationError):
        loaded.id = "renamed"
    detached = Record(id="sample")
    with pytest.raises(ValidationError):
        detached.save()
    with pytest.raises(RecordExistsError):
        detached.save(path)
    with pytest.raises(RecordExistsError):
        loaded.save(config.records_dir / "moved/sample.md")


def test_context_material_uses_the_record_registry(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    context = Record.create(
        "protocol", type="context", markdown="Guidance", project=config
    )

    assert context.path == config.records_dir / "protocol/protocol.md"
    assert load_record("protocol", project=config).type == "context"
    assert not (config.hiraku_dir / "contexts").exists()


def test_record_commit_is_local_not_inherited(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", commit="a" * 40, project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    assert child.commit is None
    assert "commit" not in child.resolve(project=config).values


@pytest.mark.parametrize(
    "content",
    ["id: no-front-matter\n", "---\n- one\n- two\n---\n", "---\nid: [\n---\n"],
)
def test_malformed_records_are_rejected(tmp_path: Path, content: str) -> None:
    config = Project.create(tmp_path)
    path = config.records_dir / "bad/bad.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValidationError):
        Record.load(path)


def test_save_markdown_preserves_metadata(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create(
        "notes", markdown="Original", project=config, custom=["value"]
    )
    metadata = record.to_dict()

    saved = record.save_markdown("## New body\n\n{{placeholder}}")

    assert type(saved) is Record
    assert record.markdown == "Original"
    assert saved.to_dict() == metadata
    loaded = Record.load(record.path)
    assert loaded.markdown == "## New body\n\n{{placeholder}}"
    assert loaded.to_dict() == metadata


@pytest.mark.parametrize("field", ["markdown", "title"])
def test_save_markdown_rejects_external_changes(tmp_path: Path, field: str) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("notes", markdown="Original", project=config)
    external = Record.load(record.path)
    setattr(external, field, "External change")
    external.save()
    contents = record.path.read_bytes()

    with pytest.raises(ValidationError, match="changed on disk"):
        record.save_markdown("My draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_rejects_detached_records() -> None:
    with pytest.raises(ValidationError, match="before editing"):
        Record(id="notes").save_markdown("Draft")


def test_save_markdown_validates_body_before_writing(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("notes", markdown="Original", project=config)
    contents = record.path.read_bytes()

    with pytest.raises(ModelValidationError, match="valid string"):
        record.save_markdown(None)

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_leaves_original_unchanged_on_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("notes", markdown="Original", project=config)
    contents = record.path.read_bytes()

    def fail_save(self) -> None:
        raise ValidationError("Cannot save record")

    monkeypatch.setattr(Record, "save", fail_save)
    with pytest.raises(ValidationError, match="Cannot save record"):
        record.save_markdown("Draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


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


def test_inheritance_and_guarded_delete(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    parent = Record.create(
        "task",
        project=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    child = Record.create(
        "exp",
        parent_id=parent.id,
        project=config,
    )
    effective = child.resolve(project=config)
    assert (
        effective.values["metrics"] == parent.resolve(project=config).values["metrics"]
    )
    assert "command" not in effective.values
    with pytest.raises(ValidationError):
        parent.delete(project=config)
    with pytest.raises(PydanticValidationError):
        Record(id="invalid", metrics=[{"name": "loss", "direction": "sideways"}])
    with pytest.raises(PydanticValidationError):
        Record(
            id="invalid",
            metrics=[{"name": "loss", "direction": "minimize", "unit": "s"}],
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("command", ["python", "train.py"]),
        ("entrypoints", {"train": {"command": ["python"]}}),
    ],
)
def test_removed_launch_fields_are_not_record_schema_fields(name, value) -> None:
    assert name not in Record.model_fields
    record = Record(id="root", **{name: value})
    assert record.model_extra[name] == value


def test_commit_local_and_validated(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    parent = Record.create("parent", commit="a" * 40, project=config)
    child = Record.create("child", parent_id=parent.id, project=config)
    assert "commit" not in child.resolve(project=config).values
    child.commit = "b" * 64
    child.save()
    assert load_record("child", project=config).commit == "b" * 64
    for bad in ("HEAD", "abcdef0", "A" * 40, "g" * 40):
        with pytest.raises((ValidationError, PydanticValidationError)):
            Record(id="bad", commit=bad)


@pytest.mark.parametrize(
    "identifier",
    ["", ".", "..", " padded", "padded ", "trailing.", "bad/name", "bad?name"],
)
def test_validate_identifier_rejects_invalid_values(identifier: str) -> None:
    with pytest.raises(ValidationError):
        validate_identifier(identifier)


def test_validate_identifier_uses_the_supplied_label() -> None:
    with pytest.raises(ValidationError, match="Invalid workflow ID"):
        validate_identifier("bad/name", label="workflow ID")
