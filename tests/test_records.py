from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.project import Project
from hiraku.record import Record, Vault, validate_identifier
from hiraku.tracking import CSVBackend, TrackingBackendFactory


def create_record(path: str, *, project: Project, **fields) -> Record:
    record = project.vault.create(path, **fields)
    project.set_active_record(record)
    return record


def test_record_is_plain_local_document_data():
    record = Record(id="experiments/baseline", type="experiment")
    assert record.id == "experiments/baseline"
    assert record.title is None
    assert not record.__private_attributes__
    for name in ("file_path", "load", "create"):
        assert not hasattr(record, name)
    with pytest.raises(PydanticValidationError):
        Record(type="task")


@pytest.mark.parametrize("path", ["", ".", "..", "/absolute", "a/../b", "a//b", "a\\b"])
def test_record_id_is_relative_and_normalized(path):
    with pytest.raises(PydanticValidationError):
        Record(id=path, type="task")


def test_local_validation_does_not_resolve_relationships():
    record = Record(id="a", type="task", parent_id="missing", related_to=["other"])
    assert record.parent_id == "missing"
    with pytest.raises(PydanticValidationError, match="duplicate"):
        Record(id="a", type="task", related_to=["other", "other"])
    with pytest.raises(PydanticValidationError):
        Record(id="a", type="task", parent_id="../outside")


def test_document_round_trip_and_metadata_snapshot(tmp_path):
    vault = Vault(root=tmp_path)
    record = Record(id="data/sample", type="Dataset", title="Sample", custom={"x": [1]})
    assert vault.save(record) == tmp_path / "data/sample.md"
    loaded = vault.load("data/sample")
    assert loaded.id == record.id
    assert loaded.metadata == record.metadata
    assert "path" not in loaded.metadata
    assert "id" not in loaded.metadata
    snapshot = loaded.metadata
    snapshot["custom"]["x"].append(2)
    assert loaded.custom == {"x": [1]}


def test_save_requires_explicit_overwrite(tmp_path):
    vault = Vault(root=tmp_path)
    record = Record(id="a", type="task", markdown="First")
    vault.save(record)
    record.markdown = "Second"
    with pytest.raises(RecordExistsError):
        vault.save(record)
    assert vault.load("a").markdown == "First"
    vault.save(record, overwrite=True)
    assert vault.load("a").markdown == "Second"


def test_same_record_can_be_saved_in_different_vaults(tmp_path):
    record = Record(id="data/a", type="Dataset")
    first, second = Vault(root=tmp_path / "first"), Vault(root=tmp_path / "second")
    first.save(record)
    second.save(record)
    assert first.file_path(record.id) != second.file_path(record.id)
    assert first.load(record.id).model_dump() == second.load(record.id).model_dump()


def test_loading_and_saving_one_file_do_not_scan_the_collection(tmp_path, monkeypatch):
    vault = Vault(root=tmp_path)
    record = Record(id="valid", type="task", related_to=["unfinished"])
    vault.save(record)
    (tmp_path / "broken.md").write_text("not front matter")

    def no_scan(self):
        pytest.fail("single-record operations must not scan the collection")

    with monkeypatch.context() as patch:
        patch.setattr(Vault, "list", no_scan)
        loaded = vault.load("valid")
        loaded.markdown = "Updated"
        vault.save(loaded, overwrite=True)
        vault.update(loaded, {"description": "Local update"})
    with pytest.raises(ValidationError):
        vault.validate()


def test_parent_relationship_is_persisted_and_independent_of_placement(tmp_path):
    vault = Vault(root=tmp_path)
    parent = Record(id="research/question", type="task")
    child = Record(id="datasets/input", type="Dataset", parent_id=parent.id)
    vault.save(parent)
    vault.save(child)
    assert vault.load(child.id).parent_id == parent.id
    assert "parent_id: research/question" in vault.file_path(child.id).read_text()
    assert [record.id for record in vault.children(parent.id)] == [child.id]
    assert [record.id for record in vault.descendants(parent.id)] == [child.id]
    assert vault.load(child.id).markdown == ""


def test_directory_placement_does_not_create_relationships(tmp_path):
    vault = Vault(root=tmp_path)
    vault.save(Record(id="root/root", type="task"))
    vault.save(Record(id="root/nested/nested", type="task"))
    assert vault.load("root/nested/nested").parent_id is None
    assert vault.children("root/root") == []


def test_fields_are_local_and_no_inheritance_api_exists(tmp_path):
    vault = Vault(root=tmp_path)
    parent = vault.create(
        "parent/parent",
        type="task",
        metrics=[{"name": "loss", "direction": "minimize"}],
        commit="a" * 40,
        title="parent",
    )
    child = vault.create(
        f"{parent.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=parent.id,
        title="child",
    )
    loaded = vault.load(child.id)
    assert loaded.metrics is None
    assert loaded.commit is None
    assert not hasattr(vault, "resolve")


def test_relationship_changes_and_cycles_are_regular_graph_data(tmp_path):
    vault = Vault(root=tmp_path)
    vault.save(Record(id="a", type="task", parent_id="b", related_to=["a"]))
    vault.save(Record(id="b", type="task", parent_id="a"))
    vault.validate()
    assert [record.id for record in vault.descendants("a")] == ["b"]
    updated = vault.update(vault.load("a"), {"parent_id": None})
    assert updated.parent_id is None
    assert vault.load("a").id == "a"


def test_workspace_checks_are_explicit(tmp_path):
    vault = Vault(root=tmp_path)
    vault.save(Record(id="a", type="task"))
    vault.save(Record(id="b", type="task"))
    assert vault.load("a").id != vault.load("b").id
    assert len(vault.list()) == 2
    vault.validate()
    vault.delete("b")
    vault.update(vault.load("a"), {"related_to": ["missing"]})
    with pytest.raises(ValidationError, match="missing related record"):
        vault.validate()


def test_delete_does_not_enforce_graph_integrity(tmp_path):
    vault = Vault(root=tmp_path)
    vault.save(Record(id="parent", type="task"))
    vault.save(Record(id="child", type="task", parent_id="parent"))
    vault.delete("parent")
    assert vault.load("child").parent_id == "parent"
    with pytest.raises(ValidationError, match="Missing parent"):
        vault.validate()
    with pytest.raises(RecordNotFoundError):
        vault.load("parent")


def test_save_revalidates_nested_mutations_without_changing_file(tmp_path):
    vault = Vault(root=tmp_path)
    record = Record(
        id="a", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    path = vault.save(record)
    before = path.read_bytes()
    record.metrics.append({"name": "accuracy", "direction": "sideways"})
    with pytest.raises(ValidationError):
        vault.save(record, overwrite=True)
    assert path.read_bytes() == before
    record = vault.load("a")
    record.custom = {"unsupported": object()}
    with pytest.raises(ValidationError, match="Unsupported YAML"):
        vault.save(record, overwrite=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("commit", ["HEAD", "abcdef0", "A" * 40, "g" * 40])
def test_commit_validation_is_local(commit):
    with pytest.raises(PydanticValidationError):
        Record(id="a", type="task", commit=commit)


def test_commit_accepts_full_git_hashes():
    for commit in ("a" * 40, "b" * 64):
        assert Record(id="a", type="task", commit=commit).commit == commit


@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "loss", "direction": "sideways"},
        {"name": "loss", "direction": "minimize", "unit": "s"},
    ],
)
def test_metric_validation(metadata):
    with pytest.raises(PydanticValidationError):
        Record(id="a", type="task", metrics=[metadata])


@pytest.mark.parametrize(
    "identifier",
    ["", ".", "..", " padded", "padded ", "trailing.", "bad/name", "bad?name"],
)
def test_validate_identifier_rejects_invalid_values(identifier):
    with pytest.raises(ValidationError):
        validate_identifier(identifier)


def test_vault_rejects_symlink_escapes(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    vault = Vault(root=root)
    record = Record(id="escape/a", type="task")
    for operation in (
        lambda: vault.save(record),
        lambda: vault.load(record.id),
        lambda: vault.delete(record.id),
    ):
        with pytest.raises(ValidationError, match="outside vault"):
            operation()
    assert list(outside.iterdir()) == []


def test_runs_list_attributed_runs_from_current_backend(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = create_record("root/root", type="task", project=config, title="root")
    child = create_record(
        f"{root.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=root.id,
        project=config,
        title="child",
    )
    with (tmp_path / "runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "record_id", "metric.loss"])
        writer.writeheader()
        writer.writerow({"id": "one", "record_id": "root/root", "metric.loss": 0.5})
        writer.writerow(
            {"id": "two", "record_id": "root/child/child", "metric.loss": 0.3}
        )
    backend = TrackingBackendFactory(config).create(config.tracking_backend)
    assert {run.id for run in backend.list_runs(record_id=root.id)} == {"one"}
    assert {run.id for run in backend.list_runs(record_id=child.id)} == {"two"}
    assert {run.id for run in backend.list_runs()} == {"one", "two"}


def test_default_run_list_returns_attributed_runs_from_selected_source(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = create_record("root/root", type="task", project=config, title="root")
    create_record(
        f"{root.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=root.id,
        project=config,
        title="child",
    )
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root/root\ntwo,root/child/child\nthree,outside\nfour,\n",
        encoding="utf-8",
    )

    backend = TrackingBackendFactory(config).create(config.tracking_backend)
    assert {run.id for run in backend.list_runs()} == {"one", "two", "three"}


def test_run_query_scans_selected_csv_once(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path, install_skill=False)
    root = create_record("root/root", type="task", project=config, title="root")
    child = create_record(
        f"{root.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=root.id,
        project=config,
        title="child",
    )
    create_record(
        f"{child.id.rpartition('/')[0]}/leaf/leaf",
        type="task",
        parent_id=child.id,
        project=config,
        title="leaf",
    )
    original = CSVBackend._read_source_runs
    calls = 0

    def count_reads(self: CSVBackend):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(CSVBackend, "_read_source_runs", count_reads)
    (config.root / "runs.csv").write_text(
        "id,record_id\none,root/root\ntwo,root/child/child\nthree,root/child/leaf/leaf\n",
        encoding="utf-8",
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
    root = create_record("root/root", type="task", project=config, title="root")
    child = create_record(
        f"{root.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=root.id,
        project=config,
        title="child",
    )
    other = create_record("other/other", type="task", project=config, title="other")

    for _record in (root, child, other):
        backend = TrackingBackendFactory(config).create(config.tracking_backend)
        assert isinstance(backend, CSVBackend)
        assert backend.path == (tmp_path / "results" / "root.csv").resolve()
