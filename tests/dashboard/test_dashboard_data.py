from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from hiraku.dashboard.data import (
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
    execution_order,
    find_run,
    format_duration,
    metric_names,
    plot_frame,
    record_rows,
    research_tree,
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


def _research_records(project: Project) -> list[Record]:
    vault = project.vault
    vault.create("t", type="task")

    def experiment(
        record_id: str, parent: str, sequence: int, **fields: object
    ) -> None:
        vault.create(
            record_id,
            type="experiment",
            parent_id=parent,
            sequence=sequence,
            summary=f"Experiment {record_id}",
            **fields,
        )

    experiment("a", "t", 1)
    experiment("b", "t", 2, commit="b" * 40)
    experiment("c", "t", 3, commit="c" * 40, state="launched")
    experiment("d", "t", 4, commit="d" * 40)
    experiment("e", "t", 5, commit="e" * 40, verdict="supports", triage="stop")
    experiment("f", "t", 6, verdict="refutes", triage="follow-up")
    experiment("g", "f", 1, verdict="inconclusive")
    experiment("h", "t", 7, verdict="supports", triage="follow-up")
    return vault.list()


def test_research_tree_states_follow_the_evidence_on_records(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    records = _research_records(project)
    runs_df = arrow_frame(
        [
            # A run at another commit does not count for b.
            {"run": "r0", "record": "b", "commit": "0" * 40, "metric.loss": 0.9},
            {"run": "r1", "record": "d", "commit": "d" * 40, "metric.loss": 0.5},
        ],
        RUN_COLUMNS,
    )

    nodes, _edges = research_tree(records, runs_df, "t")

    states = dict(zip(nodes["id"], nodes["state"], strict=True))
    assert states == {
        "t": "task",
        "a": "to implement",
        "b": "to launch",
        "c": "running",
        "d": "to analyze",
        "e": "supports",
        "f": "refutes",
        "g": "to triage",
        "h": "to design follow-ups",
    }


def test_research_tree_shows_only_experiments_and_tolerates_bad_sequences(
    tmp_path: Path,
) -> None:
    # Only experiments are steps of the study, as in the research workflow, and a
    # hand-edited sequence must not break the chart.
    project = Project.create(tmp_path, install_skill=False)
    vault = project.vault
    vault.create("t", type="task")
    vault.create("a", type="experiment", parent_id="t", sequence="1a")
    vault.create("b", type="experiment", parent_id="t", sequence=2)
    vault.create("notes", type="context", parent_id="t")
    runs_df = arrow_frame([], RUN_COLUMNS)

    nodes, _edges = research_tree(vault.list(), runs_df, "t")

    assert set(nodes["id"]) == {"t", "a", "b"}


def test_research_tree_shows_review_exits_disputes_and_acceptance(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    vault = project.vault
    vault.create(
        "t",
        type="task",
        acceptance={"outcome": "accept_with_caveats", "caveats": ["one seed"]},
    )
    reviews = [{"kind": "design"}]

    def experiment(record_id: str, sequence: int, **fields: object) -> None:
        vault.create(
            record_id,
            type="experiment",
            parent_id="t",
            sequence=sequence,
            reviews=reviews,
            **fields,
        )

    experiment("fix", 1, fixing={"kind": "code", "findings": []})
    experiment("meta", 2, meta_pending={"kind": "result", "findings": []})
    experiment("ask", 3, researcher={"stage": "design", "reason": "finding"})
    experiment("code", 4, commit="c" * 40, state="implemented")
    experiment(
        "approved", 5, commit="a" * 40, state="implemented", code_review="a" * 40
    )
    experiment("result", 6, verdict="supports", state="analyzed", commit="9" * 40)
    experiment(
        "ruled",
        7,
        verdict="inconclusive",
        state="analyzed",
        result_review="not needed",
        triage="stop",
        disputed=True,
        limitations=[{"id": "result-1.1", "claim": "One seed."}],
    )
    experiment(
        "provisional",
        8,
        verdict="supports",
        state="analyzed",
        commit="8" * 40,
        result_review="failed",
        triage="stop",
        review_failed={"stage": "result", "reason": "x", "findings": []},
    )

    nodes, _edges = research_tree(vault.list(), arrow_frame([], RUN_COLUMNS), "t")

    rows = nodes.set_index("id")
    assert rows["state"].to_dict() == {
        "t": "task",
        "fix": "fixing",
        "meta": "meta review",
        "ask": "needs researcher",
        "code": "to review",
        "approved": "to launch",
        "result": "to review",
        "ruled": "inconclusive",
        "provisional": "supports",
    }
    assert rows.loc["ruled", "disputed"]
    assert not rows.loc["fix", "disputed"]
    assert rows.loc["ruled", "note"] == "1 limitation(s)"
    assert rows.loc["provisional", "note"] == "Review failed (result)"
    assert rows.loc["t", "note"] == "Acceptance: accept_with_caveats"


def test_research_tree_shows_only_the_reviews_a_study_runs(tmp_path: Path) -> None:
    project = Project.create(tmp_path, install_skill=False)
    project.vault.create("t", type="task")
    for record_id, stages in (("result_only", ["result"]), ("with_code", ["code"])):
        project.vault.create(
            record_id,
            type="experiment",
            parent_id="t",
            commit="a" * 40,
            state="implemented",
            review_stages=stages,
        )
    project.vault.create(
        "analyzed",
        type="experiment",
        parent_id="t",
        verdict="supports",
        state="analyzed",
        review_stages=["code"],
    )

    nodes, _edges = research_tree(
        project.vault.list(), arrow_frame([], RUN_COLUMNS), "t"
    )

    states = dict(zip(nodes["id"], nodes["state"], strict=True))
    assert states["result_only"] == "to launch"
    assert states["with_code"] == "to review"
    assert states["analyzed"] == "to triage"


def test_research_tree_ignores_review_fields_without_reviews(tmp_path: Path) -> None:
    # A person's study has no reviews on its records: nothing is "to review".
    project = Project.create(tmp_path, install_skill=False)
    project.vault.create("t", type="task")
    project.vault.create(
        "a",
        type="experiment",
        parent_id="t",
        commit="a" * 40,
        state="implemented",
    )

    nodes, _edges = research_tree(
        project.vault.list(), arrow_frame([], RUN_COLUMNS), "t"
    )

    assert dict(zip(nodes["id"], nodes["state"], strict=True))["a"] == "to launch"


def test_research_tree_layout_places_parents_above_their_children(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    records = _research_records(project)

    nodes, edges = research_tree(records, arrow_frame([], RUN_COLUMNS), "t")

    placed = nodes.set_index("id")
    assert placed.loc["t", "y"] == 0
    assert placed.loc["g", "y"] == 2
    leaves = ["a", "b", "c", "d", "e", "g", "h"]
    xs = [placed.loc[leaf, "x"] for leaf in leaves]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    assert placed.loc["f", "x"] == placed.loc["g", "x"]
    assert placed.loc["t", "x"] == (placed.loc["a", "x"] + placed.loc["h", "x"]) / 2
    assert len(edges) == len(nodes) - 1
    assert placed.loc["a", "summary"] == "Experiment a"
    assert placed.loc["b", "commit"] == "bbbbbbb"


def test_research_tree_handles_unknown_tasks_and_parent_cycles(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    project.vault.create("t", type="task")
    project.vault.create("x", type="experiment", parent_id="t")
    project.vault.create("y", type="experiment", parent_id="x")
    cyclic = project.vault.load("x")
    cyclic.parent_id = "y"
    project.vault.save(cyclic, overwrite=True)
    records = project.vault.list()
    runs_df = arrow_frame([], RUN_COLUMNS)

    empty_nodes, empty_edges = research_tree(records, runs_df, "missing")
    nodes, _edges = research_tree(records, runs_df, "t")

    assert empty_nodes.empty and empty_edges.empty
    assert list(nodes["id"]) == ["t"]
