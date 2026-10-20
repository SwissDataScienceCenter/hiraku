from __future__ import annotations

import pytest

from hiraku.config import HirakuConfig
from hiraku.context import Context
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


def test_context_selection_renders_body_independently_and_refreshes(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    for identifier in ("alpha", "beta"):
        Context(id=identifier, markdown=f"## Context {identifier}").save(
            project.contexts_dir / identifier / "context.md"
        )
    from hiraku.dashboard.app import app

    _, initial = app.run()
    assert initial["selected_context"].id == "alpha"
    initial["context_selector"]._update(["beta"])
    outputs, selected = app.run(defs={"context_selector": initial["context_selector"]})
    assert selected["selected_context"].id == "beta"
    assert selected["scope"] == initial["scope"]
    assert "Context beta" in "\n".join(getattr(o, "text", "") for o in outputs)

    context = Context.load(project.contexts_dir / "beta" / "context.md")
    context.markdown = "Updated context"
    context.save()
    initial["refresh"]._update(1)
    _, refreshed = app.run(defs={"refresh": initial["refresh"]})
    assert next(c for c in refreshed["contexts"] if c.id == "beta").markdown == (
        "Updated context"
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_context_views_handle_missing_and_invalid_contexts(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch, invalid: bool
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    if invalid:
        path = project.contexts_dir / "broken" / "context.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nid: [\n---\n")
    from hiraku.dashboard.app import app

    outputs, definitions = app.run()
    text = "\n".join(getattr(o, "text", "") for o in outputs)
    assert definitions["selected_context"] is None
    assert definitions["context_selector"].value is None
    assert ("Contexts unavailable" if invalid else "No contexts yet") in text
    assert "No context selected." in text
    assert len(definitions["runs_df"]) == 5
