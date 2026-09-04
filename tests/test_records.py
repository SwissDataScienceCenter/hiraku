from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Task, list_tasks, load_task
from hiraku.errors import RecordExistsError, ValidationError


def test_markdown_round_trip_order_conflicts_and_identity(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task(
        id="sample", markdown="# Body\n\nVerbatim.\n", zeta=1, alpha={"x": True}
    )
    path = task.save(config.tasks_dir / "sample/task.md")
    loaded = Task.load(path)

    assert list(loaded.to_dict()) == ["id", "zeta", "alpha"]
    assert loaded.markdown == "# Body\n\nVerbatim.\n"
    loaded.new = [1, 2]
    loaded.save()
    assert Task.load(path).to_dict()["new"] == [1, 2]
    assert load_task("sample", config=config).id == "sample"
    assert [record.id for record in list_tasks(config=config)] == ["sample"]
    assert [record.id for record in Task.list(config.tasks_dir.glob("*/task.md"))] == [
        "sample"
    ]
    with pytest.raises(ValidationError):
        loaded.id = "renamed"
    detached = Task(id="sample")
    with pytest.raises(ValidationError):
        detached.save()
    with pytest.raises(RecordExistsError):
        detached.save(path)
    with pytest.raises(ValidationError):
        loaded.save(config.tasks_dir / "moved/task.md")


@pytest.mark.parametrize(
    "content",
    ["id: no-front-matter\n", "---\n- one\n- two\n---\n", "---\nid: [\n---\n"],
)
def test_malformed_records_are_rejected(tmp_path: Path, content: str) -> None:
    config = HirakuConfig.init(tmp_path)
    path = config.tasks_dir / "bad/task.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValidationError):
        Task.load(path)
