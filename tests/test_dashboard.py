from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hiraku import HirakuConfig
from hiraku.record import load_record


@pytest.fixture
def notebook(tmp_path: Path, monkeypatch):
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    pytest.importorskip("pandas")
    project = tmp_path.resolve()
    config = HirakuConfig.init(
        project, example="heart-disease", results=True, install_skill=False
    )
    path = Path(__file__).parents[1] / "src/hiraku/dashboard/app.py"
    spec = importlib.util.spec_from_file_location("dashboard_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(project)
    return module, config


@pytest.mark.parametrize(
    "metric,direction",
    [
        ("val/log_loss", "minimize"),
        ("val/accuracy", "maximize"),
        ("val/log_loss", None),
    ],
)
def test_chart_metrics_and_running_best(notebook, metric: str, direction: str | None):
    module, _ = notebook
    _, definitions = module.app.run()
    selector = definitions["metric_selector"]
    selector._update([metric])
    references = {
        name: definitions[name]
        for name in (
            "alt",
            "declarations",
            "json",
            "metric_selector",
            "mo",
            "numeric",
            "pd",
            "runs",
            "select_record",
            "selected_id",
            "selection_message",
            "tracking_error",
        )
    }
    if direction is None:
        references["declarations"] = []
    result = module.run_plot.run(**references)[1]
    frame = result["plot_data"]
    assert result["direction"] == direction
    assert len(frame) == 9
    assert frame.selected.sum() == 3
    assert frame.id.iloc[0] == "logistic-1"
    if direction:
        expected = (
            frame.value.cummin() if direction == "minimize" else frame.value.cummax()
        )
        assert frame.best.tolist() == expected.tolist()
    else:
        assert "best" not in frame


def test_invalid_values_source_switch_and_clicks(notebook):
    module, config = notebook
    _, definitions = module.app.run()
    numeric = definitions["numeric"]
    for value in (None, True, "bad", "nan", "inf", float("-inf")):
        assert numeric(value) is None
    assert numeric("0.5") == 0.5
    definitions["tree_buttons"]["forest-tuning"]._update(1)
    assert definitions["get_selection"]()[0] == "forest-tuning"
    definitions["on_run_click"](definitions["plot_data"].iloc[[0]])
    assert definitions["get_selection"]()[0] == "logistic-baseline"
    definitions["select_record"]("missing-record")
    assert definitions["get_selection"]()[0] == "logistic-baseline"
    assert "not in this project" in definitions["get_selection"]()[1]
    # A separate source is a test fixture, not part of the bundled example.
    (config.root / "alternate.csv").write_text(
        "id,record_id,metric.val/log_loss\n"
        "alternate-1,forest-tuning,0.2\n"
        "alternate-2,forest-tuning,0.3\n"
    )
    alternate = load_record("forest-tuning", config=config)
    alternate.tracking_backend = {"type": "csv", "path": "alternate.csv"}
    result = module.tracking_data.run(
        HirakuError=definitions["HirakuError"], config=config, record=alternate
    )[1]
    assert len(result["runs"]) == 2


def test_plot_skips_invalid_runs_and_handles_empty_sources(notebook):
    from hiraku import Run

    module, _ = notebook
    _, definitions = module.app.run()
    refs = {
        name: definitions[name]
        for name in (
            "alt",
            "declarations",
            "json",
            "metric_selector",
            "mo",
            "numeric",
            "pd",
            "runs",
            "select_record",
            "selected_id",
            "selection_message",
            "tracking_error",
        )
    }
    refs["runs"] = [
        Run(id="missing", record_id="random-forest"),
        Run(id="bad", record_id="random-forest", metrics={"val/log_loss": "nan"}),
        Run(id="outside", record_id="unknown", metrics={"val/log_loss": 0.2}),
    ]
    result = module.run_plot.run(**refs)[1]
    assert result["plot_data"].id.tolist() == ["outside"]
    result["on_run_click"](result["plot_data"])
    assert "not in this project" in definitions["get_selection"]()[1]
    refs["runs"] = []
    result = module.run_plot.run(**refs)[1]
    assert result["run_chart"] is None


def test_tree_handles_deep_and_wide_hierarchies(notebook):
    from types import SimpleNamespace

    module, _ = notebook
    _, definitions = module.app.run()
    records = [
        SimpleNamespace(
            id=f"node-{i}",
            parent_id=f"node-{i - 1}" if i else None,
            type=None,
            status=None,
            decision=None,
        )
        for i in range(1100)
    ]
    records.extend(
        SimpleNamespace(
            id=f"sibling-{i}", parent_id="node-0", type=None, status=None, decision=None
        )
        for i in range(100)
    )

    class CountedRecords(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    counted = CountedRecords(records)
    selected = []
    _, result = module.record_tree.run(
        escape=definitions["escape"],
        mo=definitions["mo"],
        project_error="",
        records=counted,
        select_record=selected.append,
        selected_id="node-1099",
    )
    buttons = result["tree_buttons"]
    assert len(buttons) == 1200
    assert counted.iterations == 1
    assert list(buttons)[:3] == ["node-0", "node-1", "node-2"]
    buttons["node-1099"]._update(1)
    assert selected == ["node-1099"]
