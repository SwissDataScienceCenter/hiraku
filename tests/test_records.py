from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError

from hiraku.agent import Context
from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import RecordExistsError, ValidationError
from hiraku.record import Record, list_records, load_record


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


@pytest.mark.parametrize("record_type", [BaseRecord, Context, Record])
def test_save_markdown_preserves_metadata_and_type(record_type, tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    record = record_type(id="notes", markdown="Original", custom=["value"])
    record.save(config.records_dir / "notes" / "notes.md")
    metadata = record.to_dict()

    saved = record.save_markdown("## New body\n\n{{placeholder}}")

    assert type(saved) is record_type
    assert record.markdown == "Original"
    assert saved.to_dict() == metadata
    loaded = record_type.load(record.path)
    assert loaded.markdown == "## New body\n\n{{placeholder}}"
    assert loaded.to_dict() == metadata


@pytest.mark.parametrize("field", ["markdown", "title"])
def test_save_markdown_rejects_external_changes(tmp_path: Path, field: str) -> None:
    record = BaseRecord(id="notes", markdown="Original")
    record.save(tmp_path / "notes.md")
    external = BaseRecord.load(record.path)
    setattr(external, field, "External change")
    external.save()
    contents = record.path.read_bytes()

    with pytest.raises(ValidationError, match="changed on disk"):
        record.save_markdown("My draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_rejects_detached_records() -> None:
    with pytest.raises(ValidationError, match="before editing"):
        BaseRecord(id="notes").save_markdown("Draft")


def test_save_markdown_validates_body_before_writing(tmp_path: Path) -> None:
    record = BaseRecord(id="notes", markdown="Original")
    record.save(tmp_path / "notes.md")
    contents = record.path.read_bytes()

    with pytest.raises(ModelValidationError, match="valid string"):
        record.save_markdown(None)

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"


def test_save_markdown_leaves_original_unchanged_on_save_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = BaseRecord(id="notes", markdown="Original")
    record.save(tmp_path / "notes.md")
    contents = record.path.read_bytes()

    def fail_save(self) -> None:
        raise ValidationError("Cannot save record")

    monkeypatch.setattr(BaseRecord, "save", fail_save)
    with pytest.raises(ValidationError, match="Cannot save record"):
        record.save_markdown("Draft")

    assert record.path.read_bytes() == contents
    assert record.markdown == "Original"
