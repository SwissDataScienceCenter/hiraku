from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from hiraku import HirakuConfig, Run
from hiraku.record import load_record

pytest.importorskip("marimo")
pytest.importorskip("altair")
pytest.importorskip("pandas")

from hiraku.dashboard import components as hd  # noqa: E402


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> HirakuConfig:
    config = HirakuConfig.init(
        tmp_path.resolve(), example="heart-disease", results=True, install_skill=False
    )
    monkeypatch.chdir(config.root)
    return config


@pytest.fixture
def notebook(project):
    path = Path(__file__).parents[1] / "src/hiraku/dashboard/app.py"
    spec = importlib.util.spec_from_file_location("dashboard_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, project


def test_numeric_rejects_invalid_values() -> None:
    for value in (None, True, "bad", "nan", "inf", float("-inf")):
        assert hd.numeric(value) is None
    assert hd.numeric("0.5") == 0.5


def test_run_click_clears_warning_for_current_record(notebook):
    module, _ = notebook
    _, definitions = module.app.run()
    select = definitions["select_record"]
    select("missing-record")
    assert definitions["get_selection"]()[1]
    frame = hd.run_frame(
        [Run(id="current", record_id="random-forest", metrics={"score": 1})],
        "score",
        "random-forest",
    )
    chart = hd.run_chart(frame, "score", select)
    chart._on_change(frame)
    assert definitions["get_selection"]() == ("random-forest", "")


def test_run_selection_disambiguates_duplicate_ids():
    frame = hd.run_frame(
        [
            Run(id="shared", record_id="first", metrics={"score": 1}),
            Run(id="shared", record_id="second", metrics={"score": 2}),
        ],
        "score",
    )
    selected = []
    chart = hd.run_chart(frame, "score", selected.append)
    spec = chart._chart.to_dict()
    assert spec["params"][0]["select"]["fields"] == ["id", "record_id"]
    chart._on_change(frame.iloc[[1]])
    assert selected == ["second"]


@pytest.mark.parametrize(
    "metric,direction",
    [
        ("val/log_loss", "minimize"),
        ("val/accuracy", "maximize"),
        ("val/log_loss", None),
    ],
)
def test_run_frame_metrics_and_running_best(
    project, metric: str, direction: str | None
):
    record = load_record("random-forest", config=project)
    declarations, runs, error = hd.load_runs(record, project)
    assert error == ""
    assert metric in hd.metric_names(declarations, runs)
    if direction:
        assert hd.metric_direction(declarations, metric) == direction
    frame = hd.run_frame(runs, metric, "random-forest", direction)
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
    assert hd.run_chart(frame, metric, print) is not None


def test_run_frame_skips_invalid_runs_and_handles_empty_sources() -> None:
    runs = [
        Run(id="missing", record_id="random-forest"),
        Run(id="bad", record_id="random-forest", metrics={"val/log_loss": "nan"}),
        Run(id="outside", record_id="unknown", metrics={"val/log_loss": 0.2}),
    ]
    frame = hd.run_frame(runs, "val/log_loss", "random-forest", "minimize")
    assert frame.id.tolist() == ["outside"]
    assert frame.position.tolist() == [3]
    assert hd.metric_names([], runs) == ["val/log_loss"]
    assert hd.run_frame([], "val/log_loss").empty


def test_load_runs_uses_record_tracking_source(project) -> None:
    # A separate source is a test fixture, not part of the bundled example.
    (project.root / "alternate.csv").write_text(
        "id,record_id,metric.val/log_loss\n"
        "alternate-1,forest-tuning,0.2\n"
        "alternate-2,forest-tuning,0.3\n"
    )
    alternate = load_record("forest-tuning", config=project)
    alternate.tracking_backend = {"type": "csv", "path": "alternate.csv"}
    assert len(hd.load_runs(alternate, project)[1]) == 2
    assert hd.load_runs(None, project) == ([], [], "")
    alternate.tracking_backend = {"type": "unknown-backend"}
    assert hd.load_runs(alternate, project)[2]


def test_load_project_outside_project(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config, records, initial_id, error = hd.load_project()
    assert (config, records, initial_id) == (None, [], None)
    assert error


def test_tree_handles_deep_and_wide_hierarchies() -> None:
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
    _, buttons = hd.record_tree(counted, "node-1099", selected.append)
    assert len(buttons) == 1200
    assert counted.iterations == 1
    assert list(buttons)[:3] == ["node-0", "node-1", "node-2"]
    buttons["node-1099"]._update(1)
    assert selected == ["node-1099"]


def test_notebook_selection_wiring(notebook):
    module, _ = notebook
    _, definitions = module.app.run()
    selection = definitions["get_selection"]
    assert definitions["run_chart"] is not None
    definitions["tree_buttons"]["forest-tuning"]._update(1)
    assert selection()[0] == "forest-tuning"
    on_change = definitions["run_chart"]._on_change
    plot_data = definitions["plot_data"]
    on_change(plot_data.assign(selected=False).iloc[[0]])
    assert selection()[0] == "logistic-baseline"
    definitions["select_record"]("missing-record")
    assert selection()[0] == "logistic-baseline"
    assert "not in this project" in selection()[1]
