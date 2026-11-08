from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.project import Project

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
def project(tmp_path: Path) -> Project:
    """A CSV-tracked tree ``root -> {a -> a1, b}`` with attributed runs."""
    config = Project.create(tmp_path, install_skill=False)
    root = config.vault.create(
        "root",
        id="root",
        type="task",
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    a = config.vault.create("a", id="a", type="task", parent_id=root.id)
    config.vault.create("b", id="b", type="task", parent_id=root.id)
    config.vault.create("a1", id="a1", type="task", parent_id=a.id)
    with (tmp_path / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["id", "record_id", "timestamp", "duration", "entrypoint", "metric.loss"]
        )
        for run_id, record_id, minute, duration, loss in RUNS:
            started = "" if minute is None else f"2026-10-14T10:0{minute}:00+00:00"
            duration = "" if duration is None else duration
            writer.writerow([run_id, record_id, started, duration, "train", loss])
    return Project.load(tmp_path / "hiraku.yml")
