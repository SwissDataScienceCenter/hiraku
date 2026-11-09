import pytest
from pydantic import ValidationError

from hiraku.record import Record, Vault, resolve_metrics


def test_resolves_nearest_metrics_without_changing_records(tmp_path):
    vault = Vault(root=tmp_path)
    parent = vault.create(
        "parent", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    child = vault.create("child", type="task", parent_id=parent.id)
    before = child.model_dump()
    assert resolve_metrics(child, vault) == parent.metrics
    assert child.model_dump() == before
    child.metrics = []
    assert resolve_metrics(child, vault) == []


@pytest.mark.parametrize("parent", [None, "missing", "child", "cycle"])
def test_unresolved_graph_returns_empty_metrics(tmp_path, parent):
    vault = Vault(root=tmp_path)
    vault.create("cycle", type="task", parent_id="child")
    record = Record(id="child", type="task", parent_id=parent)
    assert resolve_metrics(record, vault) == []


def test_malformed_parent_errors_are_not_hidden(tmp_path):
    vault = Vault(root=tmp_path)
    (tmp_path / "parent.md").write_text("---\ntitle: Missing type\n---\n")
    with pytest.raises(ValidationError):
        resolve_metrics(Record(id="child", type="task", parent_id="parent"), vault)
