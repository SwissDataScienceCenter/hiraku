from pathlib import Path

import pytest

from hiraku.errors import RecordExistsError, ValidationError
from hiraku.record import Record, Vault


def test_flat_paths_and_rename(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    parent = vault.create("parent")
    child = vault.create("child", parent_id=parent.id)
    assert parent.path == tmp_path / "parent.md"
    assert child.path == tmp_path / "child.md"
    assert vault.get(child.id).path == child.path
    child.name = "renamed"
    vault.save(child)
    assert child.path == tmp_path / "renamed.md"
    assert not (tmp_path / "child.md").exists()
    with pytest.raises(RecordExistsError):
        vault.create("parent")


def test_existing_nested_records_are_discoverable(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    record = vault.create("legacy")
    legacy = tmp_path / record.id / f"legacy--{record.id}.md"
    legacy.parent.mkdir()
    record.path.rename(legacy)
    assert vault.get(record.id).path == legacy
    vault.validate()


def test_save_is_independent_of_other_records(tmp_path: Path, monkeypatch) -> None:
    vault = Vault(dir=tmp_path)
    record = vault.create("valid")
    (tmp_path / "broken.md").write_text("not a record", encoding="utf-8")
    record.markdown = "Updated"

    def no_index():
        pytest.fail("saving one record must not scan the vault")

    with monkeypatch.context() as patch:
        patch.setattr(Vault, "_index", lambda self: no_index())
        vault.save(record)
        vault.save(Record(name="another", parent_id="missing"))

    assert "Updated" in record.path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        vault.validate()


def test_vault_validation_checks_ids_and_links(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    vault.save(Record(id="same", name="first"))
    vault.save(Record(id="same", name="second"))
    with pytest.raises(ValidationError, match="Duplicate record ID"):
        vault.validate()
    (tmp_path / "second.md").unlink()
    vault.save(Record(name="orphan", parent_id="missing"))
    with pytest.raises(ValidationError, match="Missing parent"):
        vault.validate()


def test_save_rejects_replaced_persisted_file(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    record = vault.create("original")
    record.path.write_text("---\nid: replacement\nname: original\n---\n")
    with pytest.raises(ValidationError, match="does not match"):
        vault.save(record)
