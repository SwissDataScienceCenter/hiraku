from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("marimo")
pytest.importorskip("altair")

from hiraku.dashboard import components as hc
from hiraku.dashboard.data import RECORD_COLUMNS, RUN_COLUMNS, arrow_frame


def test_run_table_retains_initial_record_and_selection_keys() -> None:
    records = arrow_frame(
        [{"record": "root", "record_path": "root", "type": "task"}], RECORD_COLUMNS
    )
    runs = arrow_frame(
        [{"run": "r1", "record": "root", "metric.loss": 0.5}], RUN_COLUMNS
    )

    table = hc.run_table(records, runs, "loss", initial_record="root")

    assert table.value[0]["key"] == "record:root"
    assert "run:r1" in table.text
    assert "0.5" in table.text


def test_run_table_accepts_an_empty_project() -> None:
    table = hc.run_table(
        arrow_frame([], RECORD_COLUMNS), arrow_frame([], RUN_COLUMNS), None
    )

    assert table.value == []


@pytest.mark.parametrize(
    ("url", "linked"),
    [("https://mlflow.example/runs/r1", True), ("javascript:alert(1)", False)],
)
def test_run_details_escapes_content_and_only_links_safe_urls(
    url: str, linked: bool
) -> None:
    details = hc.run_details(
        {"run": "<script>bad()</script>", "source": "mlflow", "url": url}
    ).text

    assert "&lt;script&gt;bad()&lt;/script&gt;" in details
    assert "<script>bad()</script>" not in details
    assert ("Open in mlflow" in details) is linked
    assert "javascript:alert" not in details


def test_metric_controls_return_widgets_with_declared_defaults() -> None:
    records = pd.DataFrame(
        {
            "metrics": [
                [
                    {"name": "loss", "direction": "minimize"},
                    {"name": "accuracy", "direction": "maximize"},
                ]
            ]
        }
    )

    controls = hc.metric_controls(records, ["loss", "accuracy"], "loss")

    assert [control.value for control in controls] == [
        "Performance over runs",
        "loss",
        "loss",
        "minimize",
        "accuracy",
        "maximize",
    ]


@pytest.mark.parametrize(
    ("mode", "x_metric", "y_metric", "message"),
    [
        ("Performance over runs", "loss", "accuracy", "No runs to plot."),
        ("Metric trade-offs", None, None, "No numeric metrics are available"),
        ("Metric trade-offs", "loss", "loss", "Choose two different metrics."),
        ("Metric trade-offs", "loss", "accuracy", "No runs have numeric values"),
    ],
)
def test_metric_analysis_handles_empty_or_unusable_inputs(
    mode: str, x_metric: str | None, y_metric: str | None, message: str
) -> None:
    runs = arrow_frame([], [*RUN_COLUMNS, "metric.loss", "metric.accuracy"])

    content = hc.metric_analysis(
        runs,
        scope="root",
        mode=mode,
        metric="loss",
        x_metric=x_metric,
        y_metric=y_metric,
        x_objective="minimize",
        y_objective="maximize",
    )

    assert message in content.text


@pytest.mark.parametrize("mode", ["Performance over runs", "Metric trade-offs"])
def test_metric_analysis_renders_runs_and_reports_excluded_values(mode: str) -> None:
    runs = arrow_frame(
        [
            {
                "run": "best",
                "record": "root",
                "metric.loss": 0.2,
                "metric.accuracy": 0.9,
            },
            {
                "run": "other",
                "record": "root",
                "metric.loss": 0.5,
                "metric.accuracy": 0.8,
            },
            {"run": "missing", "record": "root", "metric.loss": None},
        ],
        RUN_COLUMNS,
    )

    content = hc.metric_analysis(
        runs,
        scope="root",
        mode=mode,
        metric="loss",
        x_metric="loss",
        y_metric="accuracy",
        x_objective="minimize",
        y_objective="maximize",
        selected_run={"run": "best"},
    ).text

    assert "best" in content
    assert "other" in content
    if mode == "Performance over runs":
        assert "3 runs under" in content
        assert "1 without a numeric" in content
    else:
        assert "2 comparable runs" in content
        assert "1 strong trade-offs" in content
        assert "1 runs excluded" in content
