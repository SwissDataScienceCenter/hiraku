from __future__ import annotations

import pytest

from hiraku.dashboard.data import tree_rows
from hiraku.project import Project

pytest.importorskip("marimo")
pytest.importorskip("altair")


def test_app_runs_every_cell_against_a_project(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "root/a/a")
    from hiraku.resources.dashboards.dashboard.app import app

    outputs, definitions = app.run()

    rendered = "\n".join(getattr(output, "text", "") for output in outputs)
    assert "Select a run in the tree to see its details." in rendered
    assert "numbered by execution order." in rendered

    assert definitions["scope"] == "root/a/a"
    assert definitions["selected_record"].id == "root/a/a"
    assert definitions["selected_run"] is None
    assert definitions["leaderboard_error"] is None
    assert definitions["leaderboard"].source.type == "csv"
    assert [record.id for record in definitions["records"]] == [
        "root/a/a",
        "root/a/a1/a1",
        "root/b/b",
        "root/root",
    ]
    assert definitions["tree"].value[0]["key"] == "record:root/a/a"
    assert definitions["metric"].value == "loss"
    assert "plot_mode" not in definitions
    assert "Choose two different metrics." in rendered
    runs_df = definitions["runs_df"]
    assert list(runs_df["run"]) == ["r0", "r1", "r2", "r3", "r5", "r4"]
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
    [
        ("record:root/a/a", "root/a/a", "root/a/a"),
        ("run:r2", "root/a/a", "root/a/a1/a1"),
        ("run:r1", "root/b/b", "root/b/b"),
    ],
)
def test_record_details_follow_selection_without_changing_subtree_semantics(
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    scope: str,
    record_id: str,
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "root/a/a")
    from hiraku.resources.dashboards.dashboard.app import app

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
    assert f"type: {definitions['selected_record'].type}" in rendered


def test_navigation_selection_updates_table_and_clears_run(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    from hiraku.resources.dashboards.dashboard.app import app

    _, initial = app.run()
    initial["select_key"]("run:r1")
    initial["record_buttons"]["root/a/a"]._update(1)
    assert initial["get_selection"]() == ("record:root/a/a", "root/a/a")
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
    assert definitions["tree"].value[0]["key"] == "record:root/a/a"
    assert definitions["selected_record"].id == "root/a/a"
    assert definitions["selected_run"] is None


def test_project_copy_finds_its_project_without_the_launcher(
    project: Project, monkeypatch: pytest.MonkeyPatch
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

    assert definitions["project_context"].root == project.root


def test_dashboard_plots_a_leaderboard_without_records(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    (project.leaderboards_dir / "local.yml").write_text(
        "source: {type: csv, path: runs.csv}\n"
        "metrics: [{name: missing}, {name: score}]\n"
    )
    (tmp_path / "runs.csv").write_text("id,metric.score\nr,0.9\n")
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("HIRAKU_DASHBOARD_RECORD", raising=False)
    monkeypatch.delenv("HIRAKU_DASHBOARD_LEADERBOARD", raising=False)
    from hiraku.resources.dashboards.dashboard.app import app

    outputs, definitions = app.run()

    assert definitions["scope"] is None
    assert definitions["selected_record"] is None
    assert definitions["leaderboard_selector"].value == "local"
    assert definitions["metrics"] == ["missing", "score"]
    assert definitions["metric"].value == "missing"
    assert definitions["runs_df"].empty
    assert definitions["include_unattributed"].value is False
    definitions["include_unattributed"]._update(True)
    outputs, definitions = app.run(
        defs={
            name: definitions[name]
            for name in ("refresh", "leaderboard_selector", "include_unattributed")
        }
    )
    assert definitions["runs_df"]["run"].tolist() == ["r"]
    rows = tree_rows(definitions["records_df"], definitions["runs_df"], "score")
    assert [row["key"] for row in rows] == ["run:r"]
    rendered = "\n".join(getattr(output, "text", "") for output in outputs)
    assert "1 without a numeric" in rendered


def test_dashboard_selects_between_leaderboards(project, monkeypatch):
    (project.leaderboards_dir / "other.yml").write_text(
        "source: {type: csv, path: other.csv}\n"
    )
    (project.root / "other.csv").write_text(
        "id,record_id,metric.score\nother,unknown,1\n"
    )
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_LEADERBOARD", "other")
    from hiraku.resources.dashboards.dashboard.app import app

    _, definitions = app.run()
    assert definitions["leaderboard_selector"].value == "other"
    assert definitions["runs_df"]["run"].tolist() == ["other"]
    definitions["leaderboard_selector"]._update(["default"])
    _, updated = app.run(
        defs={
            "leaderboard_selector": definitions["leaderboard_selector"],
            "refresh": definitions["refresh"],
            "include_unattributed": definitions["include_unattributed"],
        }
    )
    assert len(updated["runs_df"]) == 6


@pytest.mark.parametrize("failure", ["offline", "invalid", "missing"])
def test_tracking_errors_preserve_record_navigation(project, monkeypatch, failure):
    from hiraku.errors import BackendError
    from hiraku.tracking import Leaderboard

    if failure == "offline":

        def unavailable(self, project, **filters):
            raise BackendError("source offline")

        monkeypatch.setattr(Leaderboard, "list_runs", unavailable)
    elif failure == "invalid":
        (project.leaderboards_dir / "default.yml").write_text("source: [invalid]\n")
    else:
        (project.leaderboards_dir / "default.yml").unlink()
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "root/a/a")
    monkeypatch.delenv("HIRAKU_DASHBOARD_LEADERBOARD", raising=False)
    from hiraku.resources.dashboards.dashboard.app import app

    outputs, definitions = app.run()

    assert len(definitions["records"]) == 4
    assert len(definitions["records_df"]) == 4
    assert definitions["runs_df"].empty
    assert definitions["selected_record"].id == "root/a/a"
    assert definitions["leaderboard_error"].startswith("Leaderboard unavailable:")
    if failure == "offline":
        assert definitions["leaderboard"].metrics[0].name == "loss"
        assert definitions["leaderboard_error"].endswith("source offline")
    else:
        assert definitions["leaderboard"] is None
    rendered = "\n".join(getattr(output, "text", "") for output in outputs)
    assert "Leaderboard unavailable" in rendered
