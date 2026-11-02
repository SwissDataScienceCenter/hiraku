from __future__ import annotations

import pytest
from fmql import parse_file
from pydantic import ValidationError as ModelValidationError

from hiraku.config import HirakuConfig
from hiraku.errors import RecordExistsError, RecordNotFoundError, ValidationError
from hiraku.record import (
    Record,
    list_records,
    open_workspace,
    resolve_metrics,
    validate_records,
)


@pytest.fixture
def config(tmp_path) -> HirakuConfig:
    return HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )


@pytest.mark.parametrize("field", ["id", "parent_id", "related_to"])
@pytest.mark.parametrize(
    "identifier",
    ["", ".", "..", " padded", "padded ", "trailing.", "bad/name", "bad?name", 42],
)
def test_record_identifiers_are_validated_by_pydantic(field, identifier):
    value = [identifier] if field == "related_to" else identifier
    with pytest.raises(ModelValidationError) as exc:
        Record.model_validate({"id": "valid", field: value})
    assert exc.value.errors()[0]["loc"][0] == field
    record = Record(id="valid")
    with pytest.raises(ModelValidationError):
        setattr(record, field, value)


def test_record_identifier_validation_allows_spaces_and_absent_parent():
    record = Record(id="Research goal", related_to=["Other record"])
    assert record.parent_id is None
    assert record.related_to == ["Other record"]


@pytest.mark.parametrize(
    ("middle", "local", "expected"),
    [
        (None, None, "loss"),
        ("score", None, "score"),
        ("score", "local", "local"),
        ("empty", None, None),
        (None, "empty", None),
    ],
)
def test_resolve_metrics_uses_nearest_definition(config, middle, local, expected):
    def definitions(name):
        if name is None:
            return None
        return [] if name == "empty" else [{"name": name, "direction": "minimize"}]

    workspace = open_workspace(config)
    root = Record(id="root", metrics=definitions("loss"))
    parent = Record(id="parent", parent_id="root", metrics=definitions(middle))
    child = Record(id="child", parent_id="parent", metrics=definitions(local))
    for record, path in [(root, "a.md"), (parent, "z.md"), (child, "m.md")]:
        record.save(workspace, path)

    assert [m.name for m in resolve_metrics(child, workspace)] == (
        [expected] if expected else []
    )
    assert child.metrics == Record.from_packet(parse_file(child.path)).metrics
    assert resolve_metrics(Record(id="detached"), workspace) == []


def test_markdown_round_trip_order_conflicts_and_identity(config: HirakuConfig) -> None:
    task = Record(
        id="sample", markdown="# Body\n\nVerbatim.\n", zeta=1, alpha={"x": True}
    )
    workspace = open_workspace(config)
    path = task.save(workspace, "sample/sample.md")
    loaded = Record.load(workspace, "sample")

    assert list(loaded.to_dict()) == ["id", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    loaded.save(workspace)
    assert Record.from_packet(parse_file(path)).to_dict()["new"] == [1, 2]
    assert Record.load(workspace, "sample").id == "sample"
    assert [record.id for record in list_records(workspace)] == ["sample"]
    with pytest.raises(ValidationError, match="identity"):
        loaded.model_copy(update={"id": "renamed"}).save(workspace)
    detached = Record(id="sample")
    with pytest.raises(ValidationError):
        detached.save(workspace)
    with pytest.raises(RecordExistsError):
        detached.save(workspace, path)
    with pytest.raises(RecordExistsError):
        loaded.save(workspace, "moved/sample.md")


def test_update_record_saves_a_copy_with_merged_and_removed_fields(config):
    workspace = open_workspace(config)
    Record(id="root").save(workspace, "root.md")
    original = Record.create(
        workspace,
        "child.md",
        id="child",
        parent_id="root",
        custom="old",
        markdown="Original",
    )

    updated = original.update(
        workspace, {"parent_id": None, "status": "done"}, unset=("custom",), markdown=""
    )

    assert updated.path == original.path
    assert Record.load(workspace, "child") == updated
    assert updated.parent_id is None and updated.status == "done"
    assert "custom" not in updated.to_dict() and updated.markdown == ""
    assert original.parent_id == "root" and original.custom == "old"
    assert original.markdown == "Original"


@pytest.mark.parametrize(
    ("fields", "unset"),
    [({"id": "renamed"}, ()), ({}, ("id",)), ({"custom": 2}, ("custom",))],
)
def test_update_record_rejects_identity_changes_and_conflicts(config, fields, unset):
    workspace = open_workspace(config)
    record = Record(id="one")
    path = record.save(workspace, "one.md")
    contents = path.read_bytes()
    with pytest.raises(ValidationError):
        record.update(workspace, fields, unset=unset)
    assert path.read_bytes() == contents
    assert Record.load(workspace, "one") == record


def test_relationship_context_validates_without_mutating_workspace(config):
    workspace = open_workspace(config)
    candidate = Record(id="child", parent_id="missing")
    assert candidate.parent_id == "missing"
    with pytest.raises(ModelValidationError, match="missing"):
        Record.model_validate(candidate.to_dict(), context={"workspace": workspace})
    with pytest.raises(ModelValidationError, match="missing"):
        candidate.save(workspace, "child.md")
    assert candidate.path is None
    assert not list(workspace)
    assert not (workspace.root / "child.md").exists()


def test_create_and_load_share_identity_and_destination_rules(config):
    workspace = open_workspace(config)
    record = Record.create(workspace, "nested/baseline", id="experiment")
    assert record.path == workspace.root / "nested/baseline.md"
    assert Record.load(workspace, "experiment") == record
    with pytest.raises(RecordExistsError):
        Record.create(workspace, "elsewhere", id="experiment")
    with pytest.raises(ValidationError, match="Markdown"):
        Record.create(workspace, "wrong.txt")
    with pytest.raises(ModelValidationError, match="destination"):
        Record.create(workspace, "baseline", path=record.path)


def test_context_material_uses_the_record_registry(config: HirakuConfig) -> None:
    workspace = open_workspace(config)
    context = Record.create(
        workspace,
        "protocol/protocol.md",
        id="protocol",
        type="context",
        markdown="Guidance",
    )

    assert context.path == config.records_dir / "protocol/protocol.md"
    assert Record.load(workspace, "protocol").type == "context"
    assert not (config.hiraku_dir / "contexts").exists()


def test_record_commit_is_local_not_inherited(config: HirakuConfig) -> None:
    workspace = open_workspace(config)
    root = Record.create(workspace, "root/root.md", id="root", commit="a" * 40)
    child = Record.create(workspace, "child/child.md", id="child", parent_id=root.id)
    assert child.commit is None


@pytest.mark.parametrize(
    "content",
    ["id: no-front-matter\n", "---\n- one\n- two\n---\n", "---\nid: [\n---\n"],
)
def test_malformed_records_are_rejected(config: HirakuConfig, content: str) -> None:
    path = config.records_dir / "bad/bad.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises((ValidationError, ModelValidationError)):
        workspace = open_workspace(config)
        Record.from_packet(workspace.get("bad/bad.md"))


def test_save_markdown_preserves_metadata(config: HirakuConfig) -> None:
    workspace = open_workspace(config)
    record = Record.create(
        workspace, "notes/notes.md", id="notes", markdown="Original", custom=["value"]
    )
    metadata = record.to_dict()

    saved = record.save_markdown(workspace, "## New body\n\n{{placeholder}}")

    assert type(saved) is Record
    assert record.markdown == "Original"
    assert saved.to_dict() == metadata
    loaded = Record.from_packet(parse_file(record.path))
    assert loaded.markdown == "## New body\n\n{{placeholder}}"
    assert loaded.to_dict() == metadata


def test_body_edit_does_not_require_a_valid_graph(config: HirakuConfig) -> None:
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes.md", id="notes", markdown="Original")
    (workspace.root / "unfinished.md").write_text(
        "---\nid: unfinished\nparent_id: missing\n---\nDraft"
    )

    record.save_markdown(workspace, "Edited")

    assert Record.load(workspace, "notes").markdown == "Edited"
    Record.create(workspace, "another")
    with pytest.raises(RecordNotFoundError, match="missing"):
        validate_records(workspace)


@pytest.mark.parametrize("field", ["markdown", "title"])
def test_save_markdown_rejects_external_changes(
    config: HirakuConfig, field: str
) -> None:
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes/notes.md", id="notes", markdown="Original")
    external = Record.from_packet(parse_file(record.path))
    setattr(external, field, "External change")
    external.save(open_workspace(config))
    contents = record.path.read_bytes()

    with pytest.raises(ValidationError, match="changed on disk"):
        record.save_markdown(workspace, "My draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_rejects_detached_records(config: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="before editing"):
        (Record(id="notes")).save_markdown(open_workspace(config), "Draft")


def test_save_markdown_validates_body_before_writing(config: HirakuConfig) -> None:
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes/notes.md", id="notes", markdown="Original")
    contents = record.path.read_bytes()

    with pytest.raises(ModelValidationError, match="valid string"):
        record.save_markdown(workspace, None)

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_leaves_original_unchanged_on_save_failure(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes/notes.md", id="notes", markdown="Original")
    contents = record.path.read_bytes()

    def fail_save(path, contents) -> None:
        raise OSError("Disk full")

    monkeypatch.setattr("hiraku.record.atomic_write", fail_save)
    with pytest.raises(ValidationError, match="Cannot save record"):
        record.save_markdown(workspace, "Draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"
