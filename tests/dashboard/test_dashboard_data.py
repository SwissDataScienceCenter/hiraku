from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import (
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
    default_metric,
    execution_order,
    find_run,
    format_duration,
    load,
    metric_names,
    plot_frame,
    record_rows,
    run_rows,
    run_text,
    safe_url,
    selection_scope,
    subtree,
    tradeoff_frame,
    tree_rows,
    within,
)
from hiraku.errors import BackendError
from hiraku.record import Record
from hiraku.run import Run


def tables(project: HirakuConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build ``records_df`` and ``runs_df`` as the notebook does."""
    loaded = load(project)
    return (
        arrow_frame(record_rows(loaded.records), RECORD_COLUMNS),
        arrow_frame(run_rows(loaded), RUN_COLUMNS),
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [("record:b", "b"), ("run:r2", "a"), ("run:r1", "b"), (None, "a")],
)
def test_selection_scope_preserves_subtree_for_descendant_runs(
    project: HirakuConfig, key: str | None, expected: str
) -> None:
    records, runs = tables(project)
    paths = dict(zip(records["record"], records["record_path"], strict=True))
    assert selection_scope(key, "a", paths, runs) == expected


def test_load_reads_records_and_attributed_runs(project: HirakuConfig) -> None:
    loaded = load(project)

    assert [record.id for record in loaded.records] == ["a", "a1", "b", "root"]
    # Runs attributed to records outside the project are dropped.
    assert sorted(run.id for run in loaded.runs) == ["r0", "r1", "r2", "r3", "r4"]
    assert set(loaded.sources.values()) == {"csv"}
    assert loaded.errors == []


def test_load_reports_unavailable_source_without_failing(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    Record.create("broken", config=project)

    def resolve(self: Record, **options):
        raise BackendError("source offline")

    monkeypatch.setattr(Record, "resolve_tracking_backend", resolve)

    loaded = load(project)

    assert loaded.errors == [
        f"Tracking source for {loaded.records[0].id!r} unavailable: source offline"
    ]
    assert loaded.runs == []


def test_run_rows_flatten_runs_in_execution_order(project: HirakuConfig) -> None:
    rows = run_rows(load(project))

    assert [row["run"] for row in rows] == ["r0", "r1", "r2", "r3", "r4"]
    assert rows[0]["record_path"] == "root/a"
    assert rows[0]["job_id"] is None
    assert rows[0]["source"] == "csv"
    assert rows[0]["started"] == datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert rows[0]["duration"] == 4.5
    assert float(rows[0]["metric.loss"]) == 0.9
    assert rows[2]["record_path"] == "root/a/a1"
    assert rows[-1]["started"] is None
    assert rows[-1]["duration"] is None


def test_execution_order_breaks_ties_by_id_and_puts_untimed_runs_last() -> None:
    started = datetime(2026, 10, 14, tzinfo=UTC)
    runs = [
        Run(id="untimed"),
        Run(id="b", timestamp=started),
        Run(id="a", timestamp=started),
    ]

    assert [run.id for run in execution_order(runs)] == ["a", "b", "untimed"]


def test_tradeoff_frame_identifies_non_dominated_runs() -> None:
    runs = pd.DataFrame(
        [
            {"run": "best", "record": "a", "metric.cost": 1, "metric.score": 9},
            {"run": "tradeoff", "record": "b", "metric.cost": 2, "metric.score": 10},
            {"run": "dominated", "record": "a", "metric.cost": 3, "metric.score": 8},
            {"run": "missing", "record": "b", "metric.cost": None, "metric.score": 12},
            {
                "run": "infinite",
                "record": "b",
                "metric.cost": 0,
                "metric.score": float("inf"),
            },
        ]
    )

    points, excluded = tradeoff_frame(runs, "cost", "score", "minimize", "maximize")

    assert points.loc[points["non_dominated"], "run"].tolist() == ["best", "tradeoff"]
    assert excluded == 2


def test_tradeoff_frame_accepts_arrow_metrics_with_missing_values() -> None:
    # As in the notebook: arrow_frame columns, an integer metric, and a run
    # (e.g. a crash) without metrics.
    runs = pd.DataFrame(
        {
            "run": ["a", "b", "crashed"],
            "record": ["x", "x", "y"],
            "metric.steps": pd.array([100, 200, None], dtype="int64[pyarrow]"),
            "metric.score": pd.array([0.5, 0.7, None], dtype="double[pyarrow]"),
        }
    )

    points, excluded = tradeoff_frame(runs, "steps", "score", "minimize", "maximize")

    assert points["run"].tolist() == ["a", "b"]
    assert excluded == 1


def test_record_rows_list_records_in_tree_order() -> None:
    records = [
        Record(id="root", metrics=[{"name": "loss", "direction": "minimize"}]),
        Record(id="a-b", parent_id="root"),
        Record(id="a", parent_id="root"),
        Record(id="x", parent_id="a"),
    ]

    rows = record_rows(records)

    # "a-b" sorts before "a/x" as text, but x belongs right under a.
    assert [row["record_path"] for row in rows] == [
        "root",
        "root/a",
        "root/a/x",
        "root/a-b",
    ]
    assert rows[0]["metrics"] == [{"name": "loss", "direction": "minimize"}]
    assert rows[1]["metrics"] == []


def test_arrow_frame_types_columns_and_falls_back_to_text() -> None:
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
    assert list(arrow_frame([], RUN_COLUMNS).columns) == list(RUN_COLUMNS)


def test_tree_rows_nest_runs_under_records_in_execution_order(
    project: HirakuConfig,
) -> None:
    records_df, runs_df = tables(project)

    rows = tree_rows(records_df, runs_df, "loss")

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
    assert rows[2]["Type"] == "run"
    assert rows[2]["Started"] == "2026-10-14 10:00:00"
    assert rows[-1]["Started"] == ""
    assert [row["Duration"] for row in rows] == [
        "",
        "",
        "4.5 s",
        "1 h 02 m",
        "",
        "250 ms",
        "",
        "1 m 15 s",
        "",
    ]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, ""),
        (0, "0 ms"),
        (0.25, "250 ms"),
        (4.25, "4.2 s"),
        (59.94, "59.9 s"),
        (59.96, "1 m 00 s"),
        (187, "3 m 07 s"),
        (3599.6, "1 h 00 m"),
        (3725, "1 h 02 m"),
    ],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_deep_trees_do_not_hit_the_recursion_limit() -> None:
    depth = 5000
    records = [
        Record(id=f"r{index}", parent_id=f"r{index - 1}" if index else None)
        for index in range(depth)
    ]
    records_df = arrow_frame(record_rows(records), RECORD_COLUMNS)

    rows = tree_rows(records_df, arrow_frame([], RUN_COLUMNS), None)

    assert [row["depth"] for row in rows] == list(range(depth))


def test_metric_choice_prefers_declared_metrics(project: HirakuConfig) -> None:
    records_df, _ = tables(project)
    runs_df = arrow_frame(
        [{"run": "r0", "metric.runtime": 1.0, "metric.loss": 0.1, "metric.note": "x"}]
    )

    assert metric_names(runs_df) == ["runtime", "loss"]
    assert default_metric(records_df, ["runtime", "loss"]) == "loss"
    assert default_metric(records_df, ["runtime"]) == "runtime"
    assert default_metric(records_df, []) is None


def test_find_run_and_run_text(project: HirakuConfig) -> None:
    _, runs_df = tables(project)

    run = find_run(runs_df, "r0")

    assert find_run(runs_df, "missing") is None
    # Values appear as the backend reports them; the CSV backend reports text.
    assert run_text(run) == (
        "id: r0\n"
        "job_id: null\n"
        "record: a\n"
        "commit: null\n"
        "started: '2026-10-14T10:00:00+00:00'\n"
        "duration: 4.5\n"
        "params: {}\n"
        "metrics:\n"
        "  loss: '0.9'\n"
    )


def test_subtree_selects_descendants_by_record_path(project: HirakuConfig) -> None:
    _, runs_df = tables(project)

    assert subtree(runs_df, "root/a")["run"].tolist() == ["r0", "r2", "r3"]
    assert subtree(runs_df, "root/b")["run"].tolist() == ["r1", "r4"]
    assert subtree(runs_df, None).empty
    # A sibling whose ID extends another's is not its descendant.
    assert not within("root/ab", "root/a")


def test_plot_frame_numbers_runs_and_skips_unusable_values(
    project: HirakuConfig,
) -> None:
    loss = ["0.5", "nan", "n/a", None, 0.25]
    runs_df = arrow_frame(
        [
            {"run": f"r{index}", "record": "a", "metric.loss": value}
            for index, value in enumerate(loss)
        ],
        RUN_COLUMNS,
    )

    points, missing = plot_frame(runs_df, "loss")

    assert points[["number", "run", "value"]].values.tolist() == [
        [1, "r0", 0.5],
        [5, "r4", 0.25],
    ]
    assert missing == 3
    assert plot_frame(runs_df, "accuracy")[1] == 5
    # A subtree keeps its original row labels; numbering restarts at 1.
    _, project_runs = tables(project)
    scoped, _ = plot_frame(subtree(project_runs, "root/b"), "loss")
    assert scoped[["number", "run"]].values.tolist() == [[1, "r1"], [2, "r4"]]
    assert scoped["duration"].tolist() == ["1 m 15 s", ""]


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
