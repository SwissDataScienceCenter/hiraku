from __future__ import annotations

import frontmatter
import pytest
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.record import Record, RecordUpdate, Vault, validate_identifier


def test_record_is_plain_local_document_data():
    record = Record(id="experiments/baseline", type="experiment")
    assert record.id == "experiments/baseline"
    assert record.title is None
    with pytest.raises(PydanticValidationError):
        Record(type="task")


@pytest.mark.parametrize(
    "path", ["", ".", "..", "/absolute", "a/../b", "a//b", "a\\b", "my run"]
)
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
        vault.update(loaded, RecordUpdate(description="Local update"))
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


def test_record_fields_are_local(tmp_path):
    vault = Vault(root=tmp_path)
    parent = vault.create(
        "parent/parent",
        type="task",
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
    assert loaded.commit is None


def test_relationship_changes_and_cycles_are_regular_graph_data(tmp_path):
    vault = Vault(root=tmp_path)
    vault.save(Record(id="a", type="task", parent_id="b", related_to=["a"]))
    vault.save(Record(id="b", type="task", parent_id="a"))
    vault.validate()
    assert [record.id for record in vault.descendants("a")] == ["b"]
    updated = vault.update(vault.load("a"), RecordUpdate(parent_id=None))
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
    vault.update(vault.load("a"), RecordUpdate(related_to=["missing"]))
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
    record = Record(id="a", type="task", tags=["valid"])
    path = vault.save(record)
    before = path.read_bytes()
    record.tags.append(123)
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
    "identifier",
    [
        "",
        ".",
        "..",
        " padded",
        "padded ",
        "trailing.",
        "bad/name",
        "bad?name",
        "my run",
    ],
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


def test_save_preserves_explicit_defaults_and_omits_implicit_defaults(tmp_path):
    vault = Vault(root=tmp_path)
    vault.create("minimal", type="task")
    assert frontmatter.load(tmp_path / "minimal.md").metadata == {"type": "task"}
    vault.create("explicit", type="task", status="stable", state=None, tags=[])
    assert frontmatter.load(tmp_path / "explicit.md").metadata == {
        "type": "task",
        "status": "stable",
        "state": None,
        "tags": [],
    }


def test_save_preserves_mutated_implicit_defaults(tmp_path):
    record = Record(id="example", type="task")
    record.tags.append("research")
    record.generated["by"] = "agent"
    record.related_to.append("other")
    vault = Vault(root=tmp_path)
    vault.save(record)
    loaded = vault.load(record.id)
    assert loaded.tags == ["research"]
    assert loaded.generated == {"by": "agent"}
    assert loaded.related_to == ["other"]


def test_save_preserves_complete_nested_custom_models(tmp_path):
    class Custom(BaseModel):
        label: str = "default"
        values: list[int] = Field(default_factory=list)

    custom = Custom(label="default")
    custom.values.append(1)
    vault = Vault(root=tmp_path)
    vault.create("example", type="task", custom=custom)
    assert vault.load("example").custom == {"label": "default", "values": [1]}


@pytest.mark.parametrize("status", ["draft", "stable", "deprecated"])
def test_okf_status_and_research_state_round_trip(tmp_path, status):
    vault = Vault(root=tmp_path)
    record = vault.create("experiment", type="experiment", status=status)
    assert record.state is None
    updated = vault.update(record, RecordUpdate(state="custom research phase"))
    assert updated.status == status
    loaded = vault.load(record.id)
    assert loaded.status == status
    assert loaded.state == "custom research phase"
    assert vault.update(loaded, RecordUpdate(state=None)).state is None


@pytest.mark.parametrize("status", ["concluded", "drafted", "", None, 42])
def test_invalid_okf_status_is_rejected_on_construction_load_and_assignment(
    tmp_path, status
):
    with pytest.raises(PydanticValidationError):
        Record(id="experiment", type="experiment", status=status)
    record = Record(id="experiment", type="experiment")
    assert record.status == "stable"
    with pytest.raises(PydanticValidationError):
        record.status = status
    path = tmp_path / "experiment.md"
    path.write_text(frontmatter.dumps(frontmatter.Post("", type="task", status=status)))
    with pytest.raises(PydanticValidationError):
        Vault(root=tmp_path).load("experiment")


@pytest.mark.parametrize("state", [42, ["drafted"], {"phase": "drafted"}])
def test_research_state_requires_a_string(state):
    with pytest.raises(PydanticValidationError):
        Record(id="experiment", type="experiment", state=state)


@pytest.mark.parametrize("value", ["", "  ", None, 42, ["task"]])
def test_type_is_validated_on_construction_load_and_assignment(tmp_path, value):
    with pytest.raises(PydanticValidationError):
        Record(id="example/example", title="example", type=value)
    record = Record(id="example/example", title="example", type="Dataset")
    with pytest.raises(PydanticValidationError):
        record.type = value
    path = tmp_path / "example.md"
    path.write_text(frontmatter.dumps(frontmatter.Post("", type=value)))
    with pytest.raises(PydanticValidationError):
        Vault(root=tmp_path).load(path.relative_to(tmp_path).with_suffix("").as_posix())


@pytest.mark.parametrize("relative", ["dataset.md", "data/tables/orders.md"])
def test_minimal_concept_can_be_discovered_updated_and_saved(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: Dataset\n---\nSee [future](/future.md).\n")
    vault = Vault(root=tmp_path)

    record = vault.list()[0]
    assert record.id == relative.removesuffix(".md")
    assert record.title is None
    assert vault.load(record.id).id == record.id
    vault.validate()
    record.markdown += "\n\nUpdated."
    assert vault.save(record, overwrite=True) == path
    loaded = vault.load(record.id)
    assert loaded.markdown.endswith("Updated.")
    metadata = frontmatter.load(path).metadata
    assert metadata == {"type": "Dataset"}


def test_file_location_defines_identity(tmp_path):
    path = tmp_path / "document.md"
    path.write_text("---\ntype: Reference\nid: arbitrary\n---\n")
    vault = Vault(root=tmp_path)
    record = vault.load("document")
    assert record.id == "document"
    vault.save(record, overwrite=True)
    assert "id" not in frontmatter.load(path)


def test_id_is_preserved_on_update_without_being_persisted(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("example/example", type="Dataset")
    updated = vault.update(
        record, RecordUpdate(description="Updated", markdown="# Body")
    )
    assert updated.id == record.id
    assert updated.model_dump()["id"] == record.id
    assert vault.load(record.id).id == record.id
    assert "id" not in frontmatter.load(vault.file_path(record.id))
    with pytest.raises(ValidationError, match="Cannot update"):
        vault.update(record, RecordUpdate(id="elsewhere"))


def test_identity_is_derived_from_file_placement(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("first", type="Dataset")
    vault.file_path(record.id).rename(tmp_path / "second.md")
    assert vault.load("second").id == "second"
    with pytest.raises(RecordNotFoundError):
        vault.load("first")


@pytest.mark.parametrize("title", ["Display name", "Index", "Log"])
def test_title_and_unknown_metadata_round_trip(tmp_path, title):
    path = tmp_path / "concept.md"
    path.write_text(
        f"---\ntype: Custom Concept\ntitle: {title}\n"
        "custom:\n  values: [one, two]\n---\n# Body\n"
    )
    vault = Vault(root=tmp_path)
    record = vault.list()[0]
    assert record.title == title
    vault.save(record, overwrite=True)
    assert frontmatter.load(path)["custom"] == {"values": ["one", "two"]}


@pytest.mark.parametrize("as_list", [False, True])
def test_verified_accepts_mapping_and_list(tmp_path, as_list):
    event = {"by": "human:reviewer", "at": "2026-11-09T09:00:00Z"}
    path = tmp_path / "verified.md"
    path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "", type="Reference", verified=[event] if as_list else event
            )
        )
    )
    record = Vault(root=tmp_path).load(
        path.relative_to(tmp_path).with_suffix("").as_posix()
    )
    assert record.verified == [event]
    Vault(root=tmp_path).save(record, overwrite=True)
    assert Vault(root=tmp_path).load(
        path.relative_to(tmp_path).with_suffix("").as_posix()
    ).verified == [event]


@pytest.mark.parametrize(
    "content",
    [
        "type: Dataset\n",
        "---\ntype: Dataset\n",
        "---\n- Dataset\n---\n",
        "---\n{}\n---\n",
        "---\ntitle: Missing type\n---\n",
    ],
)
def test_invalid_concept_frontmatter_is_rejected(tmp_path, content):
    path = tmp_path / "invalid.md"
    path.write_text(content)
    with pytest.raises((ValidationError, PydanticValidationError)):
        Vault(root=tmp_path).validate()


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("index.md", "---\ntype: Dataset\n---\n"),
        ("nested/index.md", "---\ntype: Reference\n---\n"),
        ("index.md", '---\nokf_version: "0.3"\n---\n'),
        ("index.md", '---\nokf_version: "0.3"\n'),
        ("log.md", "---\ntype: Log\n---\n"),
        ("log.md", "# Updates\n\n## Yesterday\n* Updated.\n"),
        ("log.md", "# Updates\n\n## 2026-02-30\n* Updated.\n"),
    ],
)
def test_reserved_documents_are_skipped_without_validating_contents(
    tmp_path, relative, content
):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    vault = Vault(root=tmp_path)
    vault.create("concept", type="Reference")
    assert [record.id for record in vault.list()] == ["concept"]
    vault.validate()


@pytest.mark.parametrize(
    "path", ["index", "log", "nested/index", "nested/log", "INDEX", "nested/LOG"]
)
def test_reserved_filenames_cannot_be_saved_as_records(tmp_path, path):
    vault = Vault(root=tmp_path)
    with pytest.raises(ValidationError, match="Reserved OKF filename"):
        vault.save(Record(id=path, type="Reference"))
    assert vault.list() == []
