from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
import yaml

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import (
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
    default_metric,
    execution_order,
    file_rows,
    find_run,
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


def test_load_resolves_shared_tracking_source_once(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Record.resolve
    calls = {"count": 0}

    def counting_resolve(self: Record, **options):
        calls["count"] += 1
        return original(self, **options)

    monkeypatch.setattr(Record, "resolve", counting_resolve)

    loaded = load(project)

    assert len(loaded.records) == 4
    assert calls["count"] == 1


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


def test_run_rows_flatten_runs_in_execution_order(project: HirakuConfig) -> None:
    rows = run_rows(load(project))

    assert [row["run"] for row in rows] == ["r0", "r1", "r2", "r3", "r4"]
    assert rows[0]["record_path"] == "root/a"
    assert rows[0]["source"] == "csv"
    assert rows[0]["started"] == datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert float(rows[0]["metric.loss"]) == 0.9
    assert rows[2]["record_path"] == "root/a/a1"
    assert rows[-1]["started"] is None


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
        "record: a\n"
        "entrypoint: train\n"
        "commit: null\n"
        "started: '2026-10-14T10:00:00+00:00'\n"
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


JOB = {
    "rule": "a__make",
    "record": "a",
    "entrypoint": "make",
    "command": "hiraku launch make --record a -- --output out.csv",
    "inputs": ["in.csv"],
    "outputs": ["out.csv"],
    "ran": True,
}


def workflow_run(run_id: str, day: int, *jobs: dict) -> Run:
    return Run(
        id=run_id,
        record_id="workflow",
        timestamp=datetime(2026, 10, day, tzinfo=UTC),
        entrypoint="snakemake",
        raw={"jobs": json.dumps(jobs)},
    )


def test_run_details_list_workflow_steps() -> None:
    from hiraku.dashboard.data import ProjectRuns

    plain = {**JOB, "rule": "copy", "record": None, "command": "cp a b", "ran": False}
    runs = [workflow_run("w1", 15, JOB, plain), Run(id="r1", record_id="a")]
    runs_df = arrow_frame(run_rows(ProjectRuns(records=[], runs=runs)), RUN_COLUMNS)

    details = yaml.safe_load(run_text(find_run(runs_df, "w1")))

    assert details["steps"] == [
        "a · make: hiraku launch make --record a -- --output out.csv",
        "rule copy: cp a b (did not run)",
    ]
    assert "steps" not in yaml.safe_load(run_text(find_run(runs_df, "r1")))


def test_file_rows_name_the_producing_step_and_its_users() -> None:
    from hiraku.dashboard.data import ProjectRuns

    use = {
        **JOB,
        "rule": "b__use",
        "record": "b",
        "entrypoint": "use",
        "inputs": ["out.csv"],
        "outputs": ["final.csv"],
    }
    runs = [
        workflow_run("w2", 16, {**JOB, "ran": False}),
        workflow_run("w1", 15, JOB, use),
        Run(id="plain", record_id="a", raw={"jobs": "not json"}),
    ]

    rows = {row["path"]: row for row in file_rows(ProjectRuns(records=[], runs=runs))}

    assert sorted(rows) == ["final.csv", "in.csv", "out.csv"]
    assert rows["out.csv"]["produced_by"] == "a · make"
    assert rows["out.csv"]["record"] == "a"
    assert rows["out.csv"]["command"] == JOB["command"]
    assert rows["out.csv"]["used_by"] == ["b · use"]
    # The later no-op run did not rebuild the file.
    assert rows["out.csv"]["last_run"] == "w1"
    assert rows["in.csv"]["produced_by"] is None
    assert rows["in.csv"]["record"] is None
    assert rows["in.csv"]["used_by"] == ["a · make"]


def _registered_record(record_id: str, *artifacts: dict, **registration: Any) -> Record:
    from hiraku.registration import Registration

    registration.setdefault("namespace", "filesystem")
    registration.setdefault("key", ["data"])
    registration.setdefault("source", "filesystem")
    registration.setdefault("coverage", "exhaustive")
    registration.setdefault("artifacts", list(artifacts))
    return Record(
        id=record_id,
        type="dataset",
        registration=Registration.model_validate(registration).to_record_value(),
    )


def test_artifact_rows_describe_registration_only() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    record = _registered_record(
        "dataset-abc",
        {
            "key": "data/a.csv",
            "display_path": "data/a.csv",
            "revision": "1:2",
            "size": 3,
            "availability": "present",
        },
    )

    rows = artifact_rows(ProjectRuns(records=[record], runs=[]))

    assert [row["dataset"] for row in rows] == ["dataset-abc"]
    assert rows[0]["source"] == "filesystem"
    assert rows[0]["path"] == "data/a.csv"
    assert rows[0]["availability"] == "present"
    assert rows[0]["coverage"] == "exhaustive"
    assert rows[0]["revision"] == "1:2"


def test_artifact_rows_render_without_runs() -> None:
    from hiraku.dashboard.data import (
        ARTIFACT_COLUMNS,
        ProjectRuns,
        arrow_frame,
        artifact_rows,
    )

    rows = artifact_rows(ProjectRuns(records=[], runs=[]))

    assert rows == []
    assert list(arrow_frame(rows, ARTIFACT_COLUMNS).columns) == list(ARTIFACT_COLUMNS)


def test_artifact_rows_skip_file_resolution_without_workflow_runs(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hiraku.dashboard.data as data

    def unexpected_resolution(self, artifact):
        raise AssertionError("no workflow files need matching")

    monkeypatch.setattr(data._WorkflowFiles, "artifact_location", unexpected_resolution)
    record = _registered_record(
        "dataset-abc",
        {"key": "data/a.csv", "display_path": "data/a.csv", "availability": "present"},
    )

    [row] = data.artifact_rows(data.ProjectRuns(records=[record], runs=[]), project)

    assert row["used_by"] == []
    assert row["built_by"] == []


def test_artifact_rows_keep_documentation_keyed_by_document_and_dataset() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    record = _registered_record(
        "dataset-abc",
        {
            "key": "data/a.csv",
            "display_path": "data/a.csv",
            "availability": "present",
            "documentation": [
                {"key": "sop-1", "role": "output"},
                {"key": "sop-2", "role": "unknown"},
            ],
        },
        documentation=[{"key": "sop-3", "role": "unknown"}],
    )

    rows = artifact_rows(ProjectRuns(records=[record], runs=[]))

    artifact = next(row for row in rows if row["key"].startswith("artifact:"))
    assert artifact["documentation"] == ["sop-1 (output)", "sop-2"]
    expectation = next(row for row in rows if row["key"].startswith("documentation:"))
    assert expectation["dataset"] == "dataset-abc"
    assert expectation["documentation"] == ["sop-3"]
    assert expectation["path"] is None


def test_artifact_rows_link_workflow_inputs_by_exact_path() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    record = _registered_record(
        "dataset-abc",
        {
            "key": "data/in.csv",
            "display_path": "data/in.csv",
            "availability": "present",
        },
    )
    use = {**JOB, "inputs": ["data/in.csv"]}
    runs = [workflow_run("w1", 15, use)]

    rows = artifact_rows(ProjectRuns(records=[record], runs=runs))

    assert rows[0]["used_by"] == [{"run": "w1", "step": "a · make"}]
    assert rows[0]["built_by"] == []


def test_artifact_rows_show_workflow_outputs_by_exact_path() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    record = _registered_record(
        "dataset-abc",
        {"key": "out", "display_path": "data/out.csv", "availability": "present"},
        {"key": "other", "display_path": "other/out.csv", "availability": "present"},
    )
    build = {**JOB, "inputs": [], "outputs": ["data/out.csv"]}
    runs = [workflow_run("w1", 15, build)]

    rows = {row["path"]: row for row in artifact_rows(ProjectRuns([record], runs))}

    assert rows["data/out.csv"]["built_by"] == [{"run": "w1", "step": "a · make"}]
    assert rows["data/out.csv"]["used_by"] == []
    assert rows["other/out.csv"]["built_by"] == []


def test_artifact_rows_without_config_never_match_external_roots() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    record = _registered_record(
        "dataset-abc",
        {
            "key": "cohort/data/in.csv",
            "display_path": "data/in.csv",
            "root": "cohort",
            "availability": "present",
        },
    )
    use = {**JOB, "inputs": ["data/in.csv"]}
    runs = [workflow_run("w1", 15, use)]

    rows = artifact_rows(ProjectRuns(records=[record], runs=runs))

    assert rows[0]["used_by"] == []
    assert rows[0]["built_by"] == []


def _cohort_project(tmp_path, mount_name: str = "cohort") -> tuple[HirakuConfig, Any]:
    """Return a project whose ``dataset_roots`` names an external cohort mount."""
    from hiraku.io import dump_yaml

    config = HirakuConfig.init(tmp_path / "project", install_skill=False)
    mount = tmp_path / "mnt" / "cohort"
    (mount / "scans").mkdir(parents=True)
    (mount / "scans" / "a.tiff").write_text("a", encoding="utf-8")
    roots = {mount_name: str(mount)}
    config.path.write_text(
        dump_yaml({**config.to_dict(), "dataset_roots": roots}), encoding="utf-8"
    )
    return HirakuConfig.load(config.path), mount


def _cohort_record(display_path: str = "scans/a.tiff", root: str = "cohort") -> Record:
    return _registered_record(
        "dataset-cohort",
        {
            "key": display_path,
            "display_path": display_path,
            "root": root,
            "availability": "present",
        },
    )


def test_artifact_rows_match_absolute_job_paths_to_named_roots(tmp_path) -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    config, mount = _cohort_project(tmp_path)
    use = {**JOB, "inputs": [str(mount / "scans" / "a.tiff")], "outputs": []}
    runs = [workflow_run("w1", 15, use)]

    [row] = artifact_rows(ProjectRuns([_cohort_record()], runs), config)

    assert row["used_by"] == [{"run": "w1", "step": "a · make"}]
    assert row["built_by"] == []


def test_artifact_rows_match_equivalent_project_paths(tmp_path) -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    config = HirakuConfig.init(tmp_path, install_skill=False)
    record = _registered_record(
        "dataset-abc",
        {"key": "in", "display_path": "data/in.csv", "availability": "present"},
    )
    spellings = ["./data/in.csv", "data/../data/in.csv", str(tmp_path / "data/in.csv")]
    runs = [
        workflow_run(f"w{index}", 15, {**JOB, "inputs": [path], "outputs": []})
        for index, path in enumerate(spellings)
    ]

    [row] = artifact_rows(ProjectRuns([record], runs), config)

    assert [entry["run"] for entry in row["used_by"]] == ["w0", "w1", "w2"]


def test_artifact_rows_resolve_symlinked_mounts(tmp_path) -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    config, mount = _cohort_project(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(mount, target_is_directory=True)
    build = {**JOB, "inputs": [], "outputs": [str(alias / "scans" / "a.tiff")]}

    [row] = artifact_rows(
        ProjectRuns([_cohort_record()], [workflow_run("w1", 15, build)]), config
    )

    assert row["built_by"] == [{"run": "w1", "step": "a · make"}]


def test_artifact_rows_match_files_inside_directory_outputs(tmp_path) -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    config, mount = _cohort_project(tmp_path)
    (mount / "scans2").mkdir()
    build = {**JOB, "inputs": [], "outputs": [str(mount / "scans")]}
    runs = [workflow_run("w1", 15, build)]
    records = [_cohort_record(), _cohort_record("scans2/b.tiff")]

    rows = {
        row["path"]: row for row in artifact_rows(ProjectRuns(records, runs), config)
    }

    assert rows["scans/a.tiff"]["built_by"] == [{"run": "w1", "step": "a · make"}]
    # A sibling directory sharing the prefix is not inside the output.
    assert rows["scans2/b.tiff"]["built_by"] == []


def test_artifact_rows_never_guess_unconfigured_roots(tmp_path) -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows

    config, mount = _cohort_project(tmp_path, mount_name="other")
    use = {**JOB, "inputs": [str(mount / "scans" / "a.tiff")], "outputs": []}
    runs = [workflow_run("w1", 15, use)]

    [row] = artifact_rows(ProjectRuns([_cohort_record()], runs), config)

    assert row["used_by"] == []
