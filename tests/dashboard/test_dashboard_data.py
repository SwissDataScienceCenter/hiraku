from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import (
    arrow_frame,
    backend_label,
    default_metric,
    execution_order,
    load,
    metric_names,
    plot_points,
    record_paths,
    record_rows,
    run_rows,
    run_text,
    safe_url,
    subtree_ids,
    tree_rows,
)
from hiraku.errors import BackendError
from hiraku.record import Record
from hiraku.run import Run


def test_load_reads_records_and_attributed_runs(project: HirakuConfig) -> None:
    loaded = load(project)

    assert [record.id for record in loaded.records] == ["a", "a1", "b", "root"]
    # Runs attributed to records outside the project are dropped.
    assert sorted(run.id for run in loaded.runs) == ["r0", "r1", "r2", "r3", "r4"]
    assert set(loaded.sources.values()) == {"csv"}
    assert loaded.errors == []


def test_run_rows_flatten_runs_in_execution_order(project: HirakuConfig) -> None:
    rows = run_rows(load(project))

    assert [row["run"] for row in rows] == ["r0", "r1", "r2", "r3", "r4"]
    first = rows[0]
    assert first["record"] == "a"
    assert first["record_path"] == "root/a"
    assert first["source"] == "csv"
    assert first["started"] == datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert first["entrypoint"] == "train"
    assert float(first["metric.loss"]) == 0.9
    assert rows[2]["record_path"] == "root/a/a1"
    assert rows[-1]["started"] is None


def test_record_rows_list_the_tree_with_declared_metrics(
    project: HirakuConfig,
) -> None:
    rows = record_rows(load(project).records)

    assert [(row["record"], row["parent"], row["record_path"]) for row in rows] == [
        ("root", None, "root"),
        ("a", "root", "root/a"),
        ("a1", "a", "root/a/a1"),
        ("b", "root", "root/b"),
    ]
    assert rows[0]["metrics"] == "loss: minimize"
    assert rows[1]["metrics"] is None


def test_record_paths_treat_missing_parents_as_roots_and_survive_cycles() -> None:
    records = [
        Record(id="orphan", parent_id="gone"),
        Record(id="child", parent_id="orphan"),
        Record(id="x", parent_id="y"),
        Record(id="y", parent_id="x"),
    ]

    paths = record_paths(records)

    assert paths["orphan"] == "orphan"
    assert paths["child"] == "orphan/child"
    assert {paths["x"], paths["y"]} == {"y/x", "y"}


def test_deep_trees_do_not_hit_the_recursion_limit() -> None:
    depth = 5000
    records = [
        Record(id=f"r{index}", parent_id=f"r{index - 1}" if index else None)
        for index in range(depth)
    ]

    rows = tree_rows(records, [], None)
    paths = record_paths(records)

    assert [row["depth"] for row in rows] == list(range(depth))
    assert paths[f"r{depth - 1}"].count("/") == depth - 1


def test_arrow_frame_types_columns_and_falls_back_to_text() -> None:
    pytest.importorskip("pyarrow")
    pd = pytest.importorskip("pandas")

    frame = arrow_frame(
        [
            {"run": "r0", "metric.loss": 0.5, "param.lr": "0.1"},
            {"run": "r1", "metric.loss": 1, "param.lr": 0.2, "param.seed": 3},
        ]
    )

    assert list(frame.columns) == ["run", "metric.loss", "param.lr", "param.seed"]
    assert all(isinstance(dtype, pd.ArrowDtype) for dtype in frame.dtypes)
    assert str(frame["metric.loss"].dtype) == "double[pyarrow]"
    assert list(frame["param.lr"]) == ["0.1", "0.2"]
    assert frame["param.seed"].isna().tolist() == [True, False]
    assert arrow_frame([]).empty


def test_load_reports_unavailable_source_without_failing(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    Record.create(
        "broken", tracking_backend={"type": "csv", "path": "other.csv"}, config=project
    )
    original = Record.resolve_tracking_backend

    def resolve(self: Record, **options):
        if self.id == "broken":
            raise BackendError("source offline")
        return original(self, **options)

    monkeypatch.setattr(Record, "resolve_tracking_backend", resolve)

    loaded = load(project)

    assert loaded.errors == ["Tracking source for 'broken' unavailable: source offline"]
    assert len(loaded.runs) == 5


def test_tree_rows_nest_runs_under_records_in_execution_order(
    project: HirakuConfig,
) -> None:
    loaded = load(project)

    rows = tree_rows(loaded.records, loaded.runs, "loss")

    assert [(row["key"], row["depth"]) for row in rows] == [
        ("record:root", 0),
        ("record:a", 1),
        ("run:r0", 2),
        ("run:r3", 2),
        ("record:a1", 2),
        ("run:r2", 3),
        ("record:b", 1),
        ("run:r1", 2),
        ("run:r4", 2),
    ]
    assert rows[0]["loss"] == ""
    assert rows[2]["loss"] == 0.9
    assert rows[2]["Started"] == "2026-10-14 10:00:00"
    assert rows[-1]["Started"] == ""


def test_tree_rows_treat_records_with_missing_parent_as_roots() -> None:
    orphan = Record(id="orphan", parent_id="gone")

    rows = tree_rows([orphan], [], None)

    assert [(row["key"], row["depth"]) for row in rows] == [("record:orphan", 0)]
    assert "loss" not in rows[0]


def test_subtree_ids(project: HirakuConfig) -> None:
    records = load(project).records

    assert subtree_ids(records, "root") == {"root", "a", "a1", "b"}
    assert subtree_ids(records, "a") == {"a", "a1"}
    assert subtree_ids(records, "b") == {"b"}


def test_execution_order_breaks_ties_by_id_and_puts_untimed_runs_last() -> None:
    started = datetime(2026, 10, 14, tzinfo=UTC)
    runs = [
        Run(id="untimed"),
        Run(id="b", timestamp=started),
        Run(id="a", timestamp=started),
    ]

    assert [run.id for run in execution_order(runs)] == ["a", "b", "untimed"]


def test_plot_points_restart_numbering_within_the_given_runs(
    project: HirakuConfig,
) -> None:
    loaded = load(project)
    scoped = [run for run in loaded.runs if run.record_id in {"a", "a1"}]

    points, missing = plot_points(scoped, "loss")

    assert [(point["number"], point["run"]) for point in points] == [
        (1, "r0"),
        (2, "r2"),
        (3, "r3"),
    ]
    assert missing == 0


def test_plot_points_skip_missing_and_non_numeric_values() -> None:
    runs = [
        Run(id="a", metrics={"loss": "0.5"}),
        Run(id="b", metrics={"loss": "nan"}),
        Run(id="c", metrics={"loss": "n/a"}),
        Run(id="d", metrics={}),
        Run(id="e", metrics={"loss": 0.25}),
    ]

    points, missing = plot_points(runs, "loss")

    assert [(point["number"], point["value"]) for point in points] == [
        (1, 0.5),
        (5, 0.25),
    ]
    assert missing == 3


def test_default_metric_prefers_declared_metrics() -> None:
    runs = [Run(id="a", metrics={"runtime": 1.0, "val/loss": 0.1})]
    declared = Record(
        id="root", metrics=[{"name": "val/loss", "direction": "minimize"}]
    )

    assert metric_names(runs) == ["runtime", "val/loss"]
    assert default_metric([declared], runs) == "val/loss"
    assert default_metric([Record(id="root")], runs) == "runtime"
    assert default_metric([declared], []) is None


def test_run_text_renders_provenance_params_and_metrics() -> None:
    run = Run(
        id="abc",
        record_id="root",
        entrypoint="train",
        commit="f" * 40,
        timestamp=datetime(2026, 10, 14, 10, tzinfo=UTC),
        params={"alpha": 0.1},
        metrics={"loss": 0.5},
    )

    assert run_text(run) == (
        "id: abc\n"
        "record: root\n"
        "entrypoint: train\n"
        f"commit: {'f' * 40}\n"
        "started: '2026-10-14T10:00:00+00:00'\n"
        "params:\n"
        "  alpha: 0.1\n"
        "metrics:\n"
        "  loss: 0.5\n"
    )


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("mlflow", "Open in MLflow"),
        ("wandb", "Open in W&B"),
        ("csv", "Open in tracking backend"),
        (None, "Open in tracking backend"),
    ],
)
def test_backend_label(source: str | None, label: str) -> None:
    assert backend_label(source) == label


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mlflow.example/#/runs/1", "https://mlflow.example/#/runs/1"),
        ("http://127.0.0.1:5000/x", "http://127.0.0.1:5000/x"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("https://user:secret@example.com", None),
        ("https://", None),
        (None, None),
    ],
)
def test_safe_url(url: str | None, expected: str | None) -> None:
    assert safe_url(url) == expected
