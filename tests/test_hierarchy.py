import pytest
from fmql import IdResolver, Query, Workspace
from pydantic import ValidationError as ModelValidationError

from hiraku import HirakuConfig
from hiraku.errors import RecordExistsError, ValidationError
from hiraku.record import (
    Record,
    list_records,
    open_workspace,
    query_records,
    validate_records,
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    return Workspace(
        root, resolvers={"parent_id": IdResolver(), "related_to": IdResolver()}
    )


def test_arbitrary_layout_and_metadata_only_reparent(workspace):
    first = Record(id="first", metrics=[{"name": "loss", "direction": "minimize"}])
    other = Record(id="other")
    middle = Record(id="middle", parent_id="first")
    leaf = Record(id="leaf", parent_id="middle")
    for record, path in [
        (first, "goals/overview.md"),
        (other, "other.md"),
        (middle, "notes/deep/unrelated.md"),
        (leaf, "leaf.md"),
    ]:
        record.save(workspace, path)
    paths = {r.id: r.path for r in list_records(workspace)}
    assert Record.load(workspace, "middle").metrics is None
    assert [
        packet.frontmatter["id"]
        for packet in Query(workspace).where(parent_id=first.id)
    ] == ["middle"]
    middle.model_copy(update={"parent_id": "other"}).save(workspace)
    assert {
        packet.frontmatter["id"]
        for packet in Query(workspace)
        .where(id=other.id)
        .follow("parent_id", direction="reverse", depth="*")
    } == {"middle", "leaf"}
    assert {r.id: r.path for r in list_records(workspace)} == paths
    middle.model_copy(update={"parent_id": None}).save(workspace)
    assert Record.load(workspace, "middle").parent_id is None
    assert {r.id: r.path for r in list_records(workspace)} == paths


def test_invalid_links_cycles_collisions_and_delete(workspace):
    first = Record.create(workspace, "first.md", id="first")
    second = Record.create(workspace, "second.md", id="second", parent_id="first")
    third = Record.create(workspace, "third.md", id="third", related_to=["second"])
    before = first.path.read_bytes()
    with pytest.raises(ModelValidationError, match="cycle"):
        first.model_copy(update={"parent_id": "second"}).save(workspace)
    assert first.path.read_bytes() == before
    assert Record.load(workspace, "first").parent_id is None
    with pytest.raises(RecordExistsError):
        Record(id="second").save(workspace, "elsewhere.md")
    with pytest.raises(ModelValidationError):
        Record(id="missing-parent", parent_id="absent").save(workspace, "bad.md")
    assert "missing-parent" not in {r.id for r in list_records(workspace)}
    second.related_to = ["missing"]
    with pytest.raises(ModelValidationError):
        second.save(workspace)
    second.related_to = []
    with pytest.raises(ValidationError, match="referenced"):
        second.delete(workspace)
    with pytest.raises(ValidationError, match="referenced"):
        first.delete(workspace)
    third.delete(workspace)
    second.delete(workspace)
    assert [r.id for r in list_records(workspace)] == ["first"]


def test_duplicate_ids_and_external_moves(workspace):
    record = Record.create(workspace, "old.md", id="stable")
    moved = workspace.root / "new.md"
    record.path.rename(moved)
    assert Record.load(workspace, "stable").path == workspace.root / "old.md"
    workspace.rescan()
    assert Record.load(workspace, "stable").path == moved
    with pytest.raises(ValidationError, match="stale"):
        record.delete(workspace)
    (workspace.root / "duplicate.md").write_bytes(moved.read_bytes())
    workspace.rescan()
    with pytest.raises(ValidationError, match="Duplicate"):
        Record.load(workspace, "stable")
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_records(workspace)
    Record.create(workspace, "another")


def test_read_only_queries_include_non_record_packets(workspace):
    (workspace.root / "guide.md").write_text("---\ntitle: Guide\n---\nInstructions")
    Record(id="one").save(workspace, "one.md")
    Record(id="two", parent_id="one").save(workspace, "two.md")
    assert query_records(
        workspace, 'MATCH (p) WHERE p.title = "Guide" RETURN p.title'
    ).rows == (("Guide",),)
    assert query_records(
        workspace, "MATCH (a)-[:parent_id]->(b) RETURN a.id, b.id"
    ).rows == (("two", "one"),)
    assert query_records(workspace, "MATCH (p) RETURN count(p)").scalar == 3
    before = (workspace.root / "one.md").read_bytes()
    for query in ['MATCH (p) SET p.status = "done" RETURN p', "MATCH (p) REMOVE p.id"]:
        with pytest.raises(ValidationError, match="read-only"):
            query_records(workspace, query)
    assert (workspace.root / "one.md").read_bytes() == before


def test_explicit_destinations_and_external_workspace(tmp_path):
    config = HirakuConfig.init(tmp_path / "code", records_path="../knowledge")
    workspace = open_workspace(config)
    record = Record.create(workspace, "nested/arbitrary.md", id="stable")
    config.set_active_record(record)
    assert record.path == tmp_path / "knowledge/nested/arbitrary.md"
    with pytest.raises(ValidationError, match="destination"):
        _created_record = Record(id="no-path")
        _created_record.save(workspace)
        config.set_active_record(_created_record)
    workspace = open_workspace(config)
    for path in ["../escape.md", "wrong.txt"]:
        with pytest.raises(ValidationError, match="Destination"):
            Record(id="bad").save(workspace, path)
    assert not (tmp_path / "escape.md").exists()
    with pytest.raises(RecordExistsError):
        Record(id="overwrite").save(workspace, "nested/arbitrary.md")


def test_format_preserving_edits_and_malformed_input(workspace):
    path = workspace.root / "note.md"
    path.write_text('---\nid: "one" # identity\nstatus: draft\n---\nBody\n')
    workspace.rescan()
    record = Record.load(workspace, "one")
    record.status = "done"
    record.save(workspace)
    assert 'id: "one" # identity' in path.read_text()
    assert path.read_text().endswith("Body\n")
    path.write_text("---\nid: [\n---\n")
    config = HirakuConfig(
        path=workspace.root / "hiraku.yml", root=workspace.root, records_path="."
    )
    with pytest.raises(ValidationError, match="Cannot read knowledge base"):
        open_workspace(config)
