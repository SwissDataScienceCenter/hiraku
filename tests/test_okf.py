"""OKF v0.2 document and bundle interoperability."""

import frontmatter
import pytest
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import RecordNotFoundError, ValidationError
from hiraku.record import Record, Vault


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
    updated = vault.update(record, {"state": "custom research phase"})
    assert updated.status == status
    loaded = vault.load(record.id)
    assert loaded.status == status
    assert loaded.state == "custom research phase"
    assert vault.update(loaded, {"state": None}).state is None


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
    assert metadata["type"] == "Dataset"
    assert "id" not in metadata
    assert "path" not in metadata
    assert "name" not in metadata
    assert "generated" not in metadata
    assert "verified" not in metadata
    assert "status" not in metadata


def test_path_ids_are_relative_to_the_bundle(tmp_path):
    for bundle in (tmp_path / "first", tmp_path / "second"):
        path = bundle / "data" / "orders.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ntype: Dataset\n---\n")
        assert Vault(root=bundle).load("data/orders").id == "data/orders"


def test_file_location_defines_identity(tmp_path):
    path = tmp_path / "document.md"
    path.write_text("---\ntype: Reference\nid: arbitrary\n---\n")
    vault = Vault(root=tmp_path)
    record = vault.load("document")
    assert record.id == "document"
    vault.save(record, overwrite=True)
    assert "id" not in frontmatter.load(path)


def test_id_is_a_required_path_not_frontmatter(tmp_path):
    record = Record(id="example/example", title="example", type="Dataset")
    vault = Vault(root=tmp_path)
    vault.save(record)
    assert record.id == "example/example"
    assert "id" not in frontmatter.load(vault.file_path(record.id))


def test_unknown_metadata_is_preserved(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text("---\ntype: Reference\ncustom_label: Source label\n---\nBody\n")
    vault = Vault(root=tmp_path)
    record = vault.load("reference")
    vault.save(record, overwrite=True)
    assert frontmatter.load(path)["custom_label"] == "Source label"


def test_id_is_preserved_on_update_without_being_persisted(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("example/example", type="Dataset")
    updated = vault.update(record, {"description": "Updated"}, markdown="# Body")
    assert updated.id == record.id
    assert updated.model_dump()["id"] == record.id
    assert vault.load(record.id).id == record.id
    assert "id" not in frontmatter.load(vault.file_path(record.id))
    with pytest.raises(ValidationError, match="Cannot update"):
        vault.update(record, {"id": "elsewhere"})


def test_local_validation_does_not_resolve_relationships(tmp_path):
    record = Record(
        id="example/example", title="example", type="Dataset", related_to=["missing"]
    )
    vault = Vault(root=tmp_path)
    vault.save(record, overwrite=True)
    with pytest.raises(ValidationError, match="missing related record"):
        vault.validate()


def test_graph_cycles_are_allowed_without_inheritance(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("example/example", type="Dataset", title="example")
    record.related_to = [record.id]
    vault.save(record, overwrite=True)
    vault.validate()


def test_identity_is_derived_from_file_placement(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("first", type="Dataset")
    vault.file_path(record.id).rename(tmp_path / "second.md")
    assert vault.load("second").id == "second"
    with pytest.raises(RecordNotFoundError):
        vault.load("first")


def test_vault_update_can_write_unfinished_relationships(tmp_path):
    vault = Vault(root=tmp_path)
    record = vault.create("example/example", type="Dataset", title="example")
    vault.update(record, {"related_to": ["missing"]})
    assert vault.load(record.id).related_to == ["missing"]
    with pytest.raises(ValidationError, match="missing related record"):
        vault.validate()


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
        ("nested/index.md", '---\nokf_version: "0.2"\n---\n'),
        ("index.md", '---\nokf_version: "0.2"\n'),
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
