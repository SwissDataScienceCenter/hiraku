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
