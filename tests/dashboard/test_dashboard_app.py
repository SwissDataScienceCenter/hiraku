from __future__ import annotations

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import tree_rows

pytest.importorskip("marimo")
pytest.importorskip("altair")


def test_app_runs_every_cell_against_a_project(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "a")
    from hiraku.dashboard.app import app

    outputs, definitions = app.run()

    rendered = "\n".join(getattr(output, "text", "") for output in outputs)
    assert "Select a run in the tree to see its details." in rendered
    assert "numbered by execution order." in rendered

    assert definitions["scope"] == "a"
    assert definitions["selected_record"].id == "a"
    assert definitions["selected_run"] is None
    assert definitions["tree"].value[0]["key"] == "record:a"
    assert definitions["metric"].value == "loss"
    assert "plot_mode" not in definitions
    assert "Choose two different metrics." in rendered
    runs_df = definitions["runs_df"]
    assert list(runs_df["run"]) == ["r0", "r1", "r2", "r3", "r4"]
    assert {"record_path", "metric.loss"} <= set(runs_df.columns)
    assert all(str(dtype).endswith("[pyarrow]") for dtype in runs_df.dtypes)
    assert list(definitions["records_df"]["record_path"]) == [
        "root",
        "root/a",
        "root/a/a1",
        "root/b",
    ]


@pytest.mark.parametrize(
    ("key", "scope", "record_id"),
    [("record:a", "a", "a"), ("run:r2", "a", "a1"), ("run:r1", "b", "b")],
)
def test_record_details_follow_selection_without_changing_subtree_semantics(
    project: HirakuConfig,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    scope: str,
    record_id: str,
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "a")
    from hiraku.dashboard.app import app

    _, initial = app.run()
    rows = tree_rows(initial["records_df"], initial["runs_df"], initial["tree_metric"])
    index = next(index for index, row in enumerate(rows) if row["key"] == key)
    initial["tree"]._update([str(index)])
    assert initial["get_selection"]() == (key, scope)
    outputs, definitions = app.run(
        defs={
            name: initial[name]
            for name in (
                "get_selection",
                "initial_record",
                "metrics",
                "paths",
                "select_key",
                "tree_metric",
            )
        }
    )

    assert definitions["scope"] == scope
    assert definitions["selected_record"].id == record_id
    assert definitions["tree"].value[0]["key"] == key
    rendered = "\n".join(getattr(output, "text", "") for output in outputs)
    assert f"id: {record_id}" in rendered


def test_navigation_selection_updates_table_and_clears_run(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    from hiraku.dashboard.app import app

    _, initial = app.run()
    initial["select_key"]("run:r1")
    initial["record_buttons"]["a"]._update(1)
    assert initial["get_selection"]() == ("record:a", "a")
    _, definitions = app.run(
        defs={
            name: initial[name]
            for name in (
                "get_selection",
                "initial_record",
                "metrics",
                "paths",
                "select_key",
                "tree_metric",
            )
        }
    )
    assert definitions["tree"].value[0]["key"] == "record:a"
    assert definitions["selected_record"].id == "a"
    assert definitions["selected_run"] is None


@pytest.mark.parametrize("linked", ["b", ["b"]])
def test_record_link_selects_that_record_over_the_start_record(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch, linked: object
) -> None:
    import marimo

    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "a")
    monkeypatch.setattr(marimo, "query_params", lambda: {"record": linked})
    from hiraku.dashboard.app import app

    _, definitions = app.run()

    assert definitions["selected_record"].id == "b"
    assert definitions["tree"].value[0]["key"] == "record:b"
    # The linked record is shown on its own branch: no sibling records.
    shown = {row["key"] for row in _rows(definitions["tree"].data)}
    assert {"record:root", "record:b"} <= shown
    assert not shown & {"record:a", "record:a1"}


def test_file_link_selects_its_dataset_and_the_file(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marimo

    from hiraku.record import Record
    from hiraku.registration import Registration

    def dataset(record_id: str, *files: str) -> None:
        Record.create(
            record_id,
            parent_id="b",
            type="dataset",
            registration=Registration.model_validate({
                "namespace": "cohort", "key": [record_id], "source": "cohort",
                "coverage": "exhaustive",
                "artifacts": [{"key": key, "display_path": f"data/{key}.csv",
                               "availability": "present"} for key in files],
            }).to_record_value(),
            config=project,
        )

    dataset("ds1", "a1_one", "a1_two")
    dataset("ds2", "a1_three")
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "a")
    monkeypatch.setattr(marimo, "query_params", lambda: {"artifact": "a1_two"})
    from hiraku.dashboard.app import app

    _, definitions = app.run()

    assert definitions["selected_record"].id == "ds1"
    files = _rows(definitions["artifact_selector"].data)
    assert [row["key"] for row in files] == [
        "artifact:ds1:a1_two",
        "artifact:ds1:a1_one",
    ]
    assert [row["key"] for row in definitions["artifact_selector"].value] == [
        "artifact:ds1:a1_two"
    ]


def _rows(data: object) -> list[dict]:
    return data.to_dict("records") if hasattr(data, "to_dict") else list(data)


def test_project_copy_finds_its_project_without_the_launcher(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marimo import App

    from hiraku.dashboard.launcher import init_dashboard

    monkeypatch.delenv("HIRAKU_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("HIRAKU_DASHBOARD_RECORD", raising=False)
    notebook = init_dashboard(project)
    namespace: dict = {"__file__": str(notebook), "__name__": "dashboard_copy"}
    exec(compile(notebook.read_text(encoding="utf-8"), notebook, "exec"), namespace)
    app: App = namespace["app"]

    _, definitions = app.run()

    assert definitions["config"].root == project.root


def test_app_exposes_workflow_files_next_to_existing_runs(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csv
    import json

    from hiraku.record import Record

    Record.create(
        "workflow",
        parent_id="root",
        tracking_backend={"type": "csv", "path": "workflow.csv"},
        config=project,
    )
    job = {
        "rule": "workflow__make",
        "record": "workflow",
        "entrypoint": "make",
        "command": "hiraku launch make --record workflow -- --output out.txt",
        "inputs": ["in.txt"],
        "outputs": ["out.txt"],
        "ran": True,
    }
    with (project.root / "workflow.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "record_id", "timestamp", "entrypoint", "jobs"])
        writer.writerow(
            ["snake_1", "workflow", "2026-10-15", "snakemake", json.dumps([job])]
        )
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    from hiraku.dashboard.app import app

    _, definitions = app.run()

    files = definitions["file_entries"]
    assert [(row["path"], row["produced_by"]) for row in files] == [
        ("in.txt", None),
        ("out.txt", "workflow · make"),
    ]
    assert files[1]["last_run"] == "snake_1"
    assert "out.txt" in definitions["file_selector"].text
    # Runs from other sources remain in the same run table.
    assert "r0" in list(definitions["runs_df"]["run"])
