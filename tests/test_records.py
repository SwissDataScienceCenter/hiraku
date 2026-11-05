from __future__ import annotations

import csv
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import (
    AmbiguousReferenceError,
    RecordExistsError,
    RecordNotFoundError,
    ValidationError,
)
from hiraku.io import atomic_write
from hiraku.project import Project
from hiraku.record import Record, Vault
from hiraku.record import validate_identifier as check_identifier
from hiraku.tracking import CSVBackend, TrackingBackendFactory


def load_record(record_id: str, *, project: Project) -> Record:
    return project.vault.get(record_id)


def list_records(*, project: Project) -> list[Record]:
    return project.vault.list()


def validate_identifier(identifier: str, *, label: str = "record ID") -> None:
    check_identifier(identifier, label=label)


def test_markdown_round_trip_order_conflicts_and_identity(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    vault = config.vault
    task = Record(
        id="sample",
        name="sample",
        markdown="# Body\n\nVerbatim.\n",
        zeta=1,
        alpha={"x": True},
    )
    path = vault.save(task)
    loaded = vault.get("sample")

    assert list(loaded.to_dict()) == ["id", "name", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    path = vault.save(loaded)
    assert vault.get("sample").to_dict()["new"] == [1, 2]
    assert load_record("sample", project=config).id == loaded.id
    assert [record.id for record in list_records(project=config)] == [loaded.id]
    assert [record.id for record in vault.list()] == [loaded.id]
    loaded.id = "renamed"
    with pytest.raises(ValidationError, match="immutable"):
        vault.save(loaded)
    detached = Record(id="sample")
    with pytest.raises(RecordExistsError):
        vault.save(detached)
    assert path.exists()


def test_context_material_uses_the_record_registry(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    context = Record.create(
        "protocol", type="context", markdown="Guidance", project=config
    )

    assert context.path == (
        config.records_dir / context.id / f"protocol--{context.id}.md"
    )
    assert load_record("protocol", project=config).type == "context"
    assert not (config.hiraku_dir / "contexts").exists()


def test_renaming_record_renames_its_markdown_file(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("initial name", project=config)
    old_path = record.path

    record.name = "new/name"
    saved_path = config.vault.save(record)

    assert saved_path == config.records_dir / record.id / f"new-name--{record.id}.md"
    assert not old_path.exists()
    assert load_record(record.id, project=config).name == "new/name"


def test_record_commit_is_local_not_inherited(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", commit="a" * 40, project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    assert child.commit is None
    assert "commit" not in config.vault.resolve(child.id)["values"]


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
        config.vault.list()


def test_paths_and_cross_root_reparenting(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    other = Record.create("other", project=config)
    middle = Record.create("middle", parent_id=first.id, project=config)
    leaf = Record.create("leaf", parent_id=middle.id, project=config)
    assert first.path == config.records_dir / first.id / f"first--{first.id}.md"
    assert middle.path == config.records_dir / first.id / f"middle--{middle.id}.md"
    assert leaf.path == config.records_dir / first.id / f"leaf--{leaf.id}.md"
    config.vault.reparent(middle.id, other.id)
    assert (
        load_record("middle", project=config).path
        == config.records_dir / other.id / f"middle--{middle.id}.md"
    )
    assert (
        load_record("leaf", project=config).path
        == config.records_dir / other.id / f"leaf--{leaf.id}.md"
    )
    assert {item.id for item in config.vault.descendants(other.id)} == {
        leaf.id,
        middle.id,
    }
    config.vault.reparent(middle.id, None)
    assert (
        load_record("middle", project=config).path
        == config.records_dir / middle.id / f"middle--{middle.id}.md"
    )
    assert (
        load_record("leaf", project=config).path
        == config.records_dir / middle.id / f"leaf--{leaf.id}.md"
    )


def test_invalid_links_cycles_collisions_and_delete(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    second = Record.create("second", parent_id=first.id, project=config)
    third = Record.create("third", related_to=[second.id], project=config)
    with pytest.raises(ValidationError, match="ancestor"):
        config.vault.reparent(first.id, second.id)
    duplicate_name = Record.create(
        "second", parent_id=first.id, project=config, activate=False
    )
    assert duplicate_name.id != second.id
    assert duplicate_name.name == second.name
    assert duplicate_name.path != second.path
    assert duplicate_name.path.name == f"second--{duplicate_name.id}.md"
    with pytest.raises(AmbiguousReferenceError):
        config.vault.get("second")
    with pytest.raises(RecordNotFoundError):
        Record.create("fourth", parent_id="missing", project=config)
    second.related_to = ["missing"]
    with pytest.raises(ValidationError):
        config.vault.save(second)
    second.related_to = []
    with pytest.raises(ValidationError, match="referenced"):
        config.vault.delete(second.id)
    config.vault.delete(third.id)
    config.vault.delete(second.id)
    config.vault.delete(duplicate_name.id)
    assert [item.id for item in list_records(project=config)] == [first.id]


def test_save_revalidates_in_place_metric_mutations(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create(
        "task",
        project=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    original = record.path.read_bytes()
    record.metrics.append({"name": "accuracy", "direction": "sideways"})

    with pytest.raises(ValidationError):
        config.vault.save(record)

    assert record.path.read_bytes() == original


def test_save_revalidates_nested_custom_field_mutations(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("task", project=config, custom={"values": []})
    original = record.path.read_bytes()
    record.custom["values"].append(object())

    with pytest.raises(ValidationError, match="Unsupported YAML value"):
        config.vault.save(record)

    assert record.path.read_bytes() == original


def test_duplicate_record_id_without_reparent_journal_is_an_error(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("duplicate", project=config)
    atomic_write(
        config.records_dir / "other" / f"{record.id}.md",
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
    assert resolved.sources["goal"] == child.id
    assert resolved.values["branch"] == "work"
    assert resolved.sources["branch"] == root.id
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
        source.id: config.records_dir / target.id / f"source--{source.id}.md",
        child.id: config.records_dir / target.id / f"child--{child.id}.md",
    }
    assert all(path.exists() for path in target_paths.values())
    assert source.parent_id == target.id
    journal = json.loads(
        (config.hiraku_dir / ".reparent-transaction.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "committed"
    assert journal["old_root"] == source.id
    assert journal["new_root"] == target.id
    assert set(journal["affected_ids"]) == {source.id, child.id}
    assert {record.id for record in list_records(project=config)} == {
        source.id,
        child.id,
        target.id,
    }
    assert load_record("source", project=config).path == target_paths[source.id]
    assert load_record("child", project=config).path == target_paths[child.id]
    assert old_paths[child.id].exists()
    with pytest.raises(ValidationError, match="pending reparent recovery"):
        load_record("child", project=config).delete(project=config)

    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert {record.id for record in list_records(project=config)} == {
        source.id,
        child.id,
        target.id,
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
    destination_child = config.records_dir / target.id / f"child--{child.id}.md"
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
    assert not (config.records_dir / target.id / f"source--{source.id}.md").exists()
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
        assert not (config.records_dir / target.id / f"source--{source.id}.md").exists()
        assert not (config.records_dir / target.id / f"child--{child.id}.md").exists()
        assert not journal_path.exists()


def test_preparing_journal_keeps_old_copies_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config, goal="old value")
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    old_child = child.path
    destination_child = config.records_dir / target.id / f"child--{child.id}.md"
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
        source.id,
        child.id,
        target.id,
    }
    assert not destination_child.exists()
    assert not (config.hiraku_dir / ".reparent-transaction.json").exists()


def test_preparing_journal_errors_when_old_authoritative_copy_is_missing(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    source = Record.create("source", project=config)
    child = Record.create("child", parent_id=source.id, project=config)
    target = Record.create("target", project=config)
    ids = [source.id, child.id]
    moves = [
        {
            "old": f"{source.id}/{record_id}.md",
            "new": f"{target.id}/{record_id}.md",
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
                "old_root": source.id,
                "new_root": target.id,
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
    target = Record.create("target", project=config)
    ids = [source.id, child.id]
    moves = [
        {
            "old": f"{source.id}/{record_id}.md",
            "new": f"{target.id}/{record_id}.md",
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
                "old_root": source.id,
                "new_root": target.id,
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
    backend = TrackingBackendFactory(config).create(config.tracking_backend)
    assert {run.id for run in backend.list_runs(record_id=root.id)} == {"one"}
    assert {run.id for run in backend.list_runs(record_id=child.id)} == {"two"}
    assert {run.id for run in backend.list_runs()} == {"one", "two"}


def test_default_run_list_returns_attributed_runs_from_selected_source(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = Record.create("root", project=config)
    Record.create("child", parent_id=root.id, project=config)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root\ntwo,child\nthree,outside\nfour,\n",
        encoding="utf-8",
    )

    backend = TrackingBackendFactory(config).create(config.tracking_backend)
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
        run.id
        for run in TrackingBackendFactory(config)
        .create(config.tracking_backend)
        .list_runs()
    } == {
        "one",
        "two",
        "three",
    }
    assert calls == 1


def test_tracking_factory_uses_project_setting(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.tracking_backend = {"type": "csv", "path": "results/root.csv"}
    root = Record.create("root", project=config)
    child = Record.create("child", parent_id=root.id, project=config)
    other = Record.create("other", project=config)

    for _record in (root, child, other):
        backend = TrackingBackendFactory(config).create(config.tracking_backend)
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


def test_project_vault_is_lazy_and_uses_project_records_directory(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path, install_skill=False)

    assert project.vault.dir == project.records_dir
    assert project.vault.list() == []
    assert project.records_dir.is_dir()


def test_vault_generates_uuid_and_gets_by_id_or_name(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    record = vault.create("baseline", markdown="# Notes\n")

    assert str(UUID(record.id)) == record.id
    assert vault.get(record.id).markdown == "# Notes\n"
    assert vault.find("baseline").id == record.id
    assert vault.list()[0].id == record.id


def test_vault_keeps_existing_readable_ids(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    record = Record(id="legacy-record", name="Legacy record")
    vault.save(record)

    assert vault.get("legacy-record").name == "Legacy record"
    assert vault.find("Legacy record").id == "legacy-record"


def test_vault_name_lookup_rejects_ambiguous_names(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    first = vault.create("experiment")
    second = vault.create("experiment")

    assert first.id != second.id
    with pytest.raises(AmbiguousReferenceError):
        vault.find("experiment")


def test_vault_checks_parent_and_cycle_on_collection_operations(
    tmp_path: Path,
) -> None:
    vault = Vault(dir=tmp_path / "records")
    parent = vault.create("parent")
    child = vault.create("child", parent_id=parent.id)
    leaf = vault.create("leaf", parent_id=child.id)

    with pytest.raises(ValidationError, match="ancestor"):
        vault.reparent(parent.id, child.id)
    assert vault.get(child.id).parent_id == parent.id

    other_root = vault.create("other root")
    old_path = vault.get(child.id).path
    moved = vault.reparent(child.id, other_root.id)

    assert moved.parent_id == other_root.id
    assert moved.path is not None
    assert moved.path.parent.name == other_root.id
    assert old_path is not None and not old_path.exists()
    assert vault.get(leaf.id).path is not None
    assert vault.get(leaf.id).path.parent.name == other_root.id
    assert {item.id for item in vault.descendants(other_root.id)} == {child.id, leaf.id}


def test_vault_rejects_duplicate_ids_and_external_paths(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    record = vault.create("one")
    assert record.path is not None
    duplicate_path = vault.dir / str(uuid4()) / "duplicate.md"
    duplicate_path.parent.mkdir(parents=True)
    duplicate_path.write_text(record.path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValidationError, match="Duplicate record ID"):
        vault.list()

    duplicate_path.unlink()
    duplicate_path.parent.rmdir()
    record._path = tmp_path / "outside.md"
    with pytest.raises(ValidationError, match="outside vault"):
        vault.save(record)


def test_vault_rejects_mutated_persisted_identity(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    record = vault.create("one")
    record.id = str(uuid4())

    with pytest.raises(ValidationError, match="immutable"):
        vault.save(record)


def test_vault_revalidates_in_place_relationship_mutations(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    record = vault.create("one")
    record.related_to.append("bad/path")

    with pytest.raises(ValidationError):
        vault.save(record)
