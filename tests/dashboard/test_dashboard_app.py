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
    assert sorted(run.id for run in definitions["scoped_runs"]) == ["r0", "r2", "r3"]
    assert definitions["selected_run"] is None
    assert definitions["tree"].value[0]["key"] == "record:a"
    assert definitions["metric"].value == "loss"
    rendered = "".join(output.text for output in outputs if hasattr(output, "text"))
    assert "hk-header" in rendered
    assert "--hk-brand-dark" in rendered
    assert "hk-footer" in rendered
