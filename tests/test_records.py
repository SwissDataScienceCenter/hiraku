from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.core.base import BaseRecord
from hiraku.core.config import HirakuConfig
from hiraku.core.errors import RecordExistsError, ValidationError
from hiraku.core.record import Record, list_records, load_record


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


def test_record_load_discards_retired_approval_metadata(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    path = config.records_dir / "task/task.md"
    path.parent.mkdir()
    path.write_text(
        "---\nid: task\ntype: experiment\napproval_status: approved\n"
        "approved_by: former-user\napproved_at_head: abc\n"
        "campaign_authorization_id: old-campaign\n---\n",
        encoding="utf-8",
    )

    record = Record.load(path)

    assert record.type == "experiment"
    assert not {
        "approval_status",
        "approved_by",
        "approved_at_head",
        "campaign_authorization_id",
    }.intersection(record.to_dict())
    record.save()
    assert "approval_status" not in path.read_text(encoding="utf-8")
