from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.views import (
    backend_action_label,
    default_plot_metrics,
    metric_names,
    param_names,
    records_frame,
    runs_frame,
    safe_run_url,
    sort_fields,
    sorted_runs,
)
from hiraku.record import Record


def _snapshot(tmp_path: Path):
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "baseline",
        config=config,
        activate=False,
        type="task",
        status="done",
    )
    Record.create("child", parent_id="baseline", config=config, activate=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,timestamp,metric.acc,param.seed\n"
        "b,child,2026-01-02T00:00:00Z,0.8,2\n"
        "a,child,2026-01-01T00:00:00Z,0.9,1\n"
        "c,baseline,2026-01-03T00:00:00Z,0.7,3\n",
        encoding="utf-8",
    )
    return build_snapshot(config, record_id="child", scope="source")


def test_records_frame_has_hierarchy_columns(tmp_path: Path) -> None:
    frame = records_frame(_snapshot(tmp_path))
    assert list(frame.columns) == ["id", "parent", "depth", "type", "status"]
    by_id = frame.set_index("id")
    assert by_id.loc["child", "parent"] == "baseline"
    assert by_id.loc["child", "depth"] == 1
    assert by_id.loc["baseline", "type"] == "task"


def test_metric_and_param_names_are_first_seen_ordered(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    assert metric_names(snapshot) == ["acc"]
    assert param_names(snapshot) == ["seed"]


def test_plot_defaults_prefer_declared_objectives_and_distinct_metrics(tmp_path):
    snapshot = _snapshot(tmp_path)
    record = next(r for r in snapshot.records if r.id == "child")
    declarations = [
        {"name": "val/loss", "direction": "minimize"},
        {"name": "val/acc", "direction": "maximize"},
        {"name": "unrecorded", "direction": "maximize"},
    ]
    snapshot = replace(
        snapshot,
        scope="record",
        records=tuple(
            replace(r, metrics=declarations) if r == record else r
            for r in snapshot.records
        ),
        runs=tuple(
            replace(
                r,
                metrics={
                    "runtime": 1,
                    "train/loss": 0.1,
                    "val/acc": 0.9,
                    "val/loss": 0.2,
                },
            )
            for r in snapshot.runs
            if r.record_id == "child"
        ),
    )
    assert default_plot_metrics(snapshot) == ("val/loss", "val/acc")
    assert default_plot_metrics(replace(snapshot, runs=())) == (None, None)
    assert default_plot_metrics(_snapshot(tmp_path / "one")) == ("acc", None)


def test_plot_defaults_do_not_invent_directions_for_undeclared_metrics(tmp_path):
    snapshot = _snapshot(tmp_path)
    snapshot = replace(
        snapshot,
        runs=tuple(
            replace(r, metrics={"runtime": 1, "loss": 0.2}) for r in snapshot.runs
        ),
    )
    assert default_plot_metrics(snapshot) == ("runtime", "loss")


def test_runs_frame_keeps_missing_values_missing(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc,param.seed\nwith,root,0.9,1\nwithout,root,,\n",
        encoding="utf-8",
    )
    snapshot = build_snapshot(config, record_id="root")

    frame = runs_frame(snapshot.runs, metrics=["acc"], params=["seed"])

    rows = frame.set_index("id")
    assert rows.loc["with", "metric.acc"] == 0.9
    assert pd.isna(rows.loc["without", "metric.acc"])
    assert rows.loc["without", "param.seed"] is None


def test_sort_fields_include_metrics_and_sorted_runs_are_cached(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    assert "metric.acc" in sort_fields(snapshot)

    ascending = sorted_runs(snapshot, "metric.acc")
    descending = sorted_runs(snapshot, "metric.acc", ascending=False)

    assert [run.id for run in ascending] == ["c", "b", "a"]
    assert [run.id for run in descending] == ["a", "b", "c"]
    assert len(snapshot.runs) == 3  # sorting did not mutate the snapshot


def test_backend_action_labels_are_explicit() -> None:
    assert backend_action_label("mlflow") == "Open in MLflow"
    assert backend_action_label("wandb") == "Open in W&B"
    assert backend_action_label("csv") == "Open in tracking backend"
    assert backend_action_label(None) == "Open in tracking backend"


def test_safe_run_url_accepts_http_and_rejects_unsafe_values() -> None:
    assert safe_run_url("https://example.com/runs/1") == ("https://example.com/runs/1")
    assert safe_run_url("http://example.com/runs/1") == "http://example.com/runs/1"
    assert safe_run_url("https://user:secret@example.com") is None
    assert safe_run_url("javascript:alert(1)") is None
    assert safe_run_url("file:///etc/passwd") is None
    assert safe_run_url(None) is None
