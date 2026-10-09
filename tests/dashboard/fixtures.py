"""Deterministic project fixture for dashboard acceptance tests."""

from __future__ import annotations

from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record


def create_acceptance_project(root: Path) -> HirakuConfig:
    """Create the issue-78 fixture project.

    Layout: one baseline, two child investigations with distinct CSV sources,
    one unrelated record, mixed metric directions, and a run with missing
    metrics. The two children reuse the same run ID to exercise source-scoped
    run identity.
    """
    config = HirakuConfig.init(root, install_skill=False)
    Record.create(
        "baseline",
        config=config,
        activate=False,
        type="task",
        status="done",
        metrics=[
            {"name": "acc", "direction": "maximize"},
            {"name": "loss", "direction": "minimize"},
        ],
        tracking_backend={"type": "csv", "path": "results/baseline.csv"},
    )
    Record.create(
        "child-a",
        parent_id="baseline",
        config=config,
        activate=False,
        status="running",
        tracking_backend={"type": "csv", "path": "results/a.csv"},
    )
    Record.create(
        "child-b",
        parent_id="baseline",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/b.csv"},
    )
    Record.create(
        "unrelated",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/unrelated.csv"},
    )
    results = root / "results"
    results.mkdir()
    (results / "baseline.csv").write_text(
        "id,record_id,timestamp,metric.acc,metric.loss\n"
        "base-1,baseline,2026-01-01T00:00:00Z,0.7,0.4\n",
        encoding="utf-8",
    )
    (results / "a.csv").write_text(
        "id,record_id,timestamp,metric.acc,metric.loss\n"
        "a-1,child-a,2026-01-02T00:00:00Z,0.8,0.3\n"
        "a-missing,child-a,2026-01-03T00:00:00Z,,\n",
        encoding="utf-8",
    )
    (results / "b.csv").write_text(
        "id,record_id,timestamp,metric.acc,metric.loss\n"
        "a-1,child-b,2026-01-04T00:00:00Z,0.9,0.2\n",
        encoding="utf-8",
    )
    (results / "unrelated.csv").write_text(
        "id,record_id,timestamp,metric.acc,metric.loss\n"
        "u-1,unrelated,2026-01-05T00:00:00Z,0.5,0.5\n",
        encoding="utf-8",
    )
    return config
