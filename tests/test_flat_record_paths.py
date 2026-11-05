from pathlib import Path

import pytest

from hiraku.errors import RecordExistsError
from hiraku.record import Vault


def test_flat_paths_rename_and_reparent(tmp_path: Path) -> None:
    vault = Vault(dir=tmp_path)
    parent = vault.create("parent")
    child = vault.create("child", parent_id=parent.id)
    assert parent.path == tmp_path / "parent.md"
    assert child.path == tmp_path / "child.md"
    vault.reparent(child.id, None)
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
