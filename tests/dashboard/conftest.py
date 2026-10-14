from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record

# (run ID, record, minute started or None, loss)
RUNS = [
    ("r0", "a", 0, 0.9),
    ("r1", "b", 1, 0.8),
    ("r2", "a1", 2, 0.5),
    ("r3", "a", 3, 0.7),
    ("r4", "b", None, 0.6),
    ("r5", "unknown", 5, 0.1),
]


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    """A CSV-tracked tree ``root -> {a -> a1, b}`` with attributed runs."""
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "root",
        type="task",
        metrics=[{"name": "loss", "direction": "minimize"}],
        tracking_backend={"type": "csv"},
        config=config,
    )
    Record.create("a", parent_id="root", config=config)
    Record.create("b", parent_id="root", config=config)
    Record.create("a1", parent_id="a", config=config)
    with (tmp_path / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "record_id", "timestamp", "entrypoint", "metric.loss"])
        for run_id, record_id, minute, loss in RUNS:
            started = "" if minute is None else f"2026-10-14T10:0{minute}:00+00:00"
            writer.writerow([run_id, record_id, started, "train", loss])
    return HirakuConfig.load(tmp_path / "hiraku.yml")
