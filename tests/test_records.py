from __future__ import annotations

import csv
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

    assert list(loaded.metadata) == ["id", "name", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim."
    loaded.new = [1, 2]
    path = vault.save(loaded)
    assert vault.get("sample").metadata["new"] == [1, 2]
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


@pytest.mark.parametrize("body", ["", "\n  leading\n\ntrailing  \n\n"])
def test_record_trims_boundary_whitespace(tmp_path: Path, body: str) -> None:
    vault = Vault(dir=tmp_path)
    record = vault.create("whitespace", markdown=body)

    assert vault.get(record.id).markdown == body.strip()


def test_frontmatter_accepts_post_constructor_field_names(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    record = vault.create("metadata", content="value", handler="other")

    loaded = vault.get(record.id)
    assert loaded.content == "value"
    assert loaded.handler == "other"


def test_record_metadata_is_a_snapshot() -> None:
    record = Record(id="sample", markdown="Body", extra={"value": 1})

    metadata = record.metadata
    assert "markdown" not in metadata
    metadata["extra"]["value"] = 2
    assert record.metadata["extra"]["value"] == 1
    record.name = "updated"
    assert record.metadata["name"] == "updated"


def test_record_load_supports_vault_save_and_identity_checks(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    original = vault.create("example", markdown="Body")
    loaded = Record.load(original.path)

    assert loaded.metadata == original.metadata
    assert loaded.markdown == "Body"
    assert loaded.path == original.path
    loaded.name = "renamed"
    assert vault.save(loaded) == tmp_path / "renamed.md"
    loaded.id = "other"
    with pytest.raises(ValidationError, match="immutable"):
        vault.save(loaded)


def test_context_material_uses_the_record_registry(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    context = Record.create(
        "protocol", type="context", markdown="Guidance", project=config
    )

    assert context.path == config.records_dir / "protocol.md"
    assert load_record("protocol", project=config).type == "context"
    assert not (config.hiraku_dir / "contexts").exists()


def test_renaming_record_renames_its_markdown_file(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("initial name", project=config)
    old_path = record.path

    record.name = "new/name"
    saved_path = config.vault.save(record)

    assert saved_path == config.records_dir / "new-name.md"
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


def test_flat_paths_preserve_hierarchy(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    middle = Record.create("middle", parent_id=first.id, project=config)
    leaf = Record.create("leaf", parent_id=middle.id, project=config)
    assert first.path == config.records_dir / "first.md"
    assert middle.path == config.records_dir / "middle.md"
    assert leaf.path == config.records_dir / "leaf.md"
    assert load_record("middle", project=config).path == middle.path
    assert load_record("leaf", project=config).path == leaf.path
    assert {item.id for item in config.vault.descendants(first.id)} == {
        leaf.id,
        middle.id,
    }


def test_invalid_links_cycles_collisions_and_delete(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", project=config)
    second = Record.create("second", parent_id=first.id, project=config)
    third = Record.create("third", related_to=[second.id], project=config)
    first.parent_id = second.id
    with pytest.raises(ValidationError, match="parent is immutable"):
        config.vault.save(first)
    with pytest.raises(RecordExistsError):
        Record.create("second", parent_id=first.id, project=config, activate=False)
    with pytest.raises(RecordNotFoundError):
        Record.create("fourth", parent_id="missing", project=config)
    second.related_to = ["missing"]
    with pytest.raises(ValidationError):
        config.vault.validate_relationships(second)
    second.related_to = []
    with pytest.raises(ValidationError, match="referenced"):
        config.vault.delete(second.id)
    config.vault.delete(third.id)
    config.vault.delete(second.id)
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


def test_duplicate_record_id_is_an_error(
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
    resolved = config.vault.resolve(child.id)
    assert all(
        key not in resolved["values"]
        for key in ("goal", "branch", "status", "decision", "type", "command")
    )


def test_record_parent_is_immutable_after_save(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path / "records")
    parent = vault.create("parent")
    child = vault.create("child", parent_id=parent.id)
    child.parent_id = None

    with pytest.raises(ValidationError, match="parent is immutable"):
        vault.save(child)

    assert vault.get(child.id).parent_id == parent.id


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
    effective = config.vault.resolve(child.id)
    assert (
        effective["values"]["metrics"]
        == (config.vault.resolve(parent.id)["values"]["metrics"])
    )
    assert "command" not in effective["values"]
    with pytest.raises(ValidationError):
        config.vault.delete(parent.id)
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
    assert "commit" not in config.vault.resolve(child.id)["values"]
    child.commit = "b" * 64
    config.vault.save(child)
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
    assert vault.get(record.id).markdown == "# Notes"
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
    legacy = vault.dir / "legacy" / "experiment.md"
    atomic_write(legacy, first.path.read_text().replace(first.id, str(uuid4())))
    with pytest.raises(AmbiguousReferenceError):
        vault.find("experiment")


def test_vault_checks_parent_and_cycle_on_collection_operations(
    tmp_path: Path,
) -> None:
    vault = Vault(dir=tmp_path / "records")
    parent = vault.create("parent")
    child = vault.create("child", parent_id=parent.id)
    leaf = vault.create("leaf", parent_id=child.id)

    parent.parent_id = leaf.id
    with pytest.raises(ValidationError, match="cycle"):
        vault.validate_relationships(parent)
    assert vault.get(child.id).parent_id == parent.id
    assert {item.id for item in vault.descendants(parent.id)} == {child.id, leaf.id}


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
