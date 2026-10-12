from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import RecordExistsError, ValidationError
from hiraku.record import Record, list_records, load_record, update_record


def test_markdown_round_trip_order_conflicts_and_identity(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = BaseRecord(
        id="sample", markdown="# Body\n\nVerbatim.\n", zeta=1, alpha={"x": True}
    )
    path = task.save(config.records_dir / "sample/sample.md")
    loaded = BaseRecord.load(path)

    assert list(loaded.to_dict()) == ["id", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    loaded.save()
    assert BaseRecord.load(path).to_dict()["new"] == [1, 2]
    assert load_record("sample", config=config).id == "sample"
    assert [record.id for record in list_records(config=config)] == ["sample"]
    assert [
        record.id for record in BaseRecord.list(config.records_dir.glob("*/*.md"))
    ] == ["sample"]
    with pytest.raises(ValidationError):
        loaded.id = "renamed"
    detached = BaseRecord(id="sample")
    with pytest.raises(ValidationError):
        detached.save()
    with pytest.raises(RecordExistsError):
        detached.save(path)
    with pytest.raises(ValidationError):
        loaded.save(config.records_dir / "moved/sample.md")


@pytest.mark.parametrize(
    "content",
    ["id: no-front-matter\n", "---\n- one\n- two\n---\n", "---\nid: [\n---\n"],
)
def test_malformed_records_are_rejected(tmp_path: Path, content: str) -> None:
    config = HirakuConfig.init(tmp_path)
    path = config.records_dir / "bad/bad.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValidationError):
        Record.load(path)


def test_update_record_sets_unsets_and_moves(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    Record.create("root", config=config, metrics=[])
    Record.create("other", config=config)
    Record.create("child", parent_id="root", config=config, summary="old", note="x")

    updated = update_record(
        "child",
        changes={"summary": "new"},
        unset=["note"],
        markdown="Body",
        config=config,
    )
    stored = load_record("child", config=config)
    assert updated.summary == stored.summary == "new"
    assert "note" not in stored.to_dict()
    assert stored.markdown == "Body"

    moved = update_record("child", parent_id="other", config=config)
    assert moved.path == config.records_dir / "other" / "child.md"
    rooted = update_record("child", make_root=True, config=config)
    assert rooted.parent_id is None
    assert load_record("child", config=config).path == (
        config.records_dir / "child" / "child.md"
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"changes": {"id": "x"}}, "dedicated operations"),
        ({"changes": {"parent_id": "x"}}, "dedicated operations"),
        ({"unset": ["parent_id"]}, "Cannot unset parent_id"),
        ({"changes": {"a": 1}, "unset": ["a"]}, "set and unset"),
        ({"parent_id": "root", "make_root": True}, "mutually exclusive"),
        ({"changes": {"commit": "abc"}}, "full lowercase Git hash"),
    ],
)
def test_update_record_rejects_invalid_changes(
    tmp_path: Path, arguments: dict, message: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    Record.create("root", config=config)
    Record.create("child", parent_id="root", config=config)
    with pytest.raises(ValidationError, match=message):
        update_record("child", config=config, **arguments)
