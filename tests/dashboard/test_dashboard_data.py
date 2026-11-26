from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from hiraku.dashboard.data import (
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
    execution_order,
    filter_leaderboard_runs,
    find_run,
    format_duration,
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
from hiraku.project import Project
from hiraku.record import Record
from hiraku.tracking import Leaderboard, Run


def tables(project: Project) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build ``records_df`` and ``runs_df`` as the notebook does."""
    records = project.vault.list()
    leaderboard = Leaderboard.load(project)
    runs = leaderboard.list_runs(project)
    return (
        arrow_frame(record_rows(records), RECORD_COLUMNS),
        arrow_frame(
            run_rows(runs, records=records, source=leaderboard.source.type), RUN_COLUMNS
        ),
    )


@pytest.mark.parametrize(
    ("direct", "descendants", "unattributed", "expected"),
    [
        (False, False, False, ["direct", "child", "other"]),
        (True, False, False, ["direct"]),
        (False, True, False, ["child"]),
        (True, True, True, ["direct", "child", "unattributed"]),
    ],
)
def test_leaderboard_filter_combines_run_sets(
    project, direct, descendants, unattributed, expected
):
    runs = arrow_frame(
        [
            {"run": "direct", "record": "root/a/a"},
            {"run": "child", "record": "root/a/a1/a1"},
            {"run": "other", "record": "root/b/b"},
            {"run": "unattributed", "record": None},
        ],
        RUN_COLUMNS,
    )
    records = project.vault.list()
    for record in records:
        record.title = "Same title"
    filtered = filter_leaderboard_runs(
        runs,
        records,
        "root/a/a",
        include_record=direct,
        include_descendants=descendants,
        include_unattributed=unattributed,
    )
    assert filtered["run"].tolist() == expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("record:root/b/b", "root/b/b"),
        ("run:r2", "root/a/a"),
        ("run:r1", "root/b/b"),
        (None, None),
    ],
)
def test_selection_scope_preserves_subtree_for_descendant_runs(
    project: Project, key: str | None, expected: str
) -> None:
    records, runs = tables(project)
    paths = dict(zip(records["record"], records["record_path"], strict=True))
    assert selection_scope(key, "root/a/a", paths, runs) == expected


def test_run_rows_keep_runs_without_matching_records() -> None:
    records = [Record(id="known", type="task")]
    rows = run_rows(
        [Run(id="a", record_id="known"), Run(id="b", record_id="unknown"), Run(id="c")],
        records=records,
        source="csv",
    )
    assert [row["run"] for row in rows] == ["a", "b", "c"]
    assert [row["record_path"] for row in rows] == ["known", None, None]
    assert [row["source"] for row in rows] == ["csv", "csv", "csv"]


def test_run_rows_flatten_runs_in_execution_order(project: Project) -> None:
    leaderboard = Leaderboard.load(project)
    rows = run_rows(
        leaderboard.list_runs(project),
        records=project.vault.list(),
        source=leaderboard.source.type,
    )

    assert [row["run"] for row in rows] == ["r0", "r1", "r2", "r3", "r5", "r4"]
    assert rows[0]["record_path"] == "root/a"
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
        Record(
            id="root",
            type="task",
        ),
        Record(id="a-b", type="record", parent_id="root"),
        Record(id="a", type="record", parent_id="root"),
        Record(id="x", type="record", parent_id="a"),
    ]

    rows = record_rows(records)

    # "a-b" sorts before "a/x" as text, but x belongs right under a.
    assert [row["record_path"] for row in rows] == [
        "root",
        "root/a",
        "root/a/x",
        "root/a-b",
    ]


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
    project: Project,
) -> None:
    records_df, runs_df = tables(project)

    rows = tree_rows(records_df, runs_df, "loss")

    assert [(row["key"], row["depth"]) for row in rows] == [
        ("record:root/root", 0),
        ("record:root/a/a", 1),
        ("run:r0", 2),
        ("run:r3", 2),
        ("record:root/a/a1/a1", 2),
        ("run:r2", 3),
        ("record:root/b/b", 1),
        ("run:r1", 2),
        ("run:r4", 2),
        ("run:r5", 0),
    ]
    assert rows[0]["loss"] == ""
    assert rows[2]["loss"] == 0.9
    assert rows[2]["Type"] == "run"
    assert rows[2]["Started"] == "2026-10-14 10:00:00"
    assert rows[-2]["Started"] == ""
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
        "1.0 s",
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
        Record(
            id=f"r{index}",
            type="record",
            parent_id=f"r{index - 1}" if index else None,
        )
        for index in range(depth)
    ]
    records_df = arrow_frame(record_rows(records), RECORD_COLUMNS)

    rows = tree_rows(records_df, arrow_frame([], RUN_COLUMNS), None)

    assert [row["depth"] for row in rows] == list(range(depth))


def test_metric_names_exclude_non_numeric_columns() -> None:
    runs_df = arrow_frame(
        [{"run": "r0", "metric.runtime": 1.0, "metric.loss": 0.1, "metric.note": "x"}]
    )
    assert metric_names(runs_df) == ["runtime", "loss"]


def test_find_run_and_run_text(project: Project) -> None:
    _, runs_df = tables(project)

    run = find_run(runs_df, "r0")

    assert find_run(runs_df, "missing") is None
    assert "dataset" not in runs_df.columns
    # Dashboard rows use the normalized Run model's numeric metric values.
    assert run_text(run) == (
        "id: r0\n"
        "record: root/a/a\n"
        "commit: null\n"
        "started: '2026-10-14T10:00:00+00:00'\n"
        "duration: 4.5\n"
        "params: {}\n"
        "metrics:\n"
        "  loss: 0.9\n"
    )


def test_subtree_selects_descendants_by_record_id(project: Project) -> None:
    _, runs_df = tables(project)

    assert subtree(runs_df, "root/a")["run"].tolist() == ["r0", "r2", "r3"]
    assert subtree(runs_df, "root/b")["run"].tolist() == ["r1", "r4"]
    assert subtree(runs_df, None).equals(runs_df)
    # A sibling whose ID extends another's is not its descendant.
    assert not within("root/ab", "root/a")


def test_plot_frame_numbers_runs_and_skips_unusable_values(
    project: Project,
) -> None:
    loss = ["0.5", "nan", "n/a", None, 0.25]
    runs_df = arrow_frame(
        [
            {"run": f"r{index}", "record": "root/a/a", "metric.loss": value}
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
