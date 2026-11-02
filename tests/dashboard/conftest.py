from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace

# (run ID, record, minute started or None, seconds taken or None, loss)
RUNS = [
    ("r0", "a", 0, 4.5, 0.9),
    ("r1", "b", 1, 75, 0.8),
    ("r2", "a1", 2, 0.25, 0.5),
    ("r3", "a", 3, 3725, 0.7),
    ("r4", "b", None, None, 0.6),
    ("r5", "unknown", 5, 1, 0.1),
]


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    """A CSV-tracked tree ``root -> {a -> a1, b}`` with attributed runs."""
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    _created_record = Record.create(
        workspace,
        "root/root.md",
        id="root",
        type="task",
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    config.set_active_record(_created_record)
    _created_record = Record.create(workspace, "a/a.md", id="a", parent_id="root")
    config.set_active_record(_created_record)
    _created_record = Record.create(workspace, "b/b.md", id="b", parent_id="root")
    config.set_active_record(_created_record)
    _created_record = Record.create(workspace, "a1/a1.md", id="a1", parent_id="a")
    config.set_active_record(_created_record)
    with (tmp_path / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["id", "record_id", "timestamp", "duration", "entrypoint", "metric.loss"]
        )
        for run_id, record_id, minute, duration, loss in RUNS:
            started = "" if minute is None else f"2026-10-14T10:0{minute}:00+00:00"
            duration = "" if duration is None else duration
            writer.writerow([run_id, record_id, started, duration, "train", loss])
    return HirakuConfig.load(tmp_path / "hiraku.yml")
