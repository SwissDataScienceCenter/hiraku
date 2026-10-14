from __future__ import annotations

import pytest

from hiraku.config import HirakuConfig

pytest.importorskip("marimo")
pytest.importorskip("altair")


def test_app_runs_every_cell_against_a_project(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_PROJECT_ROOT", str(project.root))
    monkeypatch.setenv("HIRAKU_DASHBOARD_RECORD", "a")
    from hiraku.dashboard.app import app

    outputs, definitions = app.run()

    assert definitions["scope"] == "a"
    assert definitions["scoped_runs"]["run"].tolist() == ["r0", "r2", "r3"]
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
    rendered = "".join(output.text for output in outputs if hasattr(output, "text"))
    assert "hk-header" in rendered
    assert "--hk-brand-dark" in rendered
    assert "hk-footer" in rendered


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
