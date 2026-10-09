from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.data import RunView
from hiraku.dashboard.plots import (
    metric_direction,
    pareto_points,
    progress_points,
)
from hiraku.record import Record


def _run(
    run_id: str,
    metrics: dict[str, object],
    *,
    timestamp: datetime | None = None,
    record_id: str = "root",
    params: dict[str, object] | None = None,
) -> RunView:
    return RunView(
        key=f"csv:::{run_id}",
        id=run_id,
        record_id=record_id,
        timestamp=timestamp,
        commit="a" * 40,
        entrypoint=None,
        url=None,
        metrics=metrics,
        params=params or {},
        source="csv:::",
    )


def test_progress_uses_index_when_timestamps_are_missing() -> None:
    data = progress_points(
        [
            _run("a", {"acc": 0.9}, timestamp=datetime(2026, 1, 1, tzinfo=UTC)),
            _run("b", {"acc": 0.8}),
        ],
        "acc",
    )

    assert list(data.frame["x_kind"]) == ["run_index", "run_index"]
    assert list(data.frame["run_id"]) == ["a", "b"]
    assert data.omitted == 0


def test_progress_uses_timestamps_when_all_present() -> None:
    data = progress_points(
        [
            _run("a", {"acc": 0.9}, timestamp=datetime(2026, 1, 1, tzinfo=UTC)),
            _run("b", {"acc": 0.8}, timestamp=datetime(2026, 1, 2, tzinfo=UTC)),
        ],
        "acc",
    )

    assert list(data.frame["x_kind"]) == ["timestamp", "timestamp"]
    assert list(data.frame["x"]) == [
        datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000,
        datetime(2026, 1, 2, tzinfo=UTC).timestamp() * 1000,
    ]
    assert list(data.frame["run_id"]) == ["a", "b"]


def test_progress_excludes_missing_and_builds_best_so_far() -> None:
    data = progress_points(
        [
            _run("a", {"acc": 0.5}),
            _run("b", {"acc": 0.9}),
            _run("c", {"acc": float("nan")}),
            _run("d", {"acc": 0.7}),
        ],
        "acc",
        direction="maximize",
    )

    assert list(data.frame["run_id"]) == ["a", "b", "d"]
    assert list(data.frame["best_so_far"]) == [0.5, 0.9, 0.9]
    assert data.omitted == 1


def test_progress_infinity_is_omitted() -> None:
    data = progress_points(
        [
            _run("a", {"acc": 0.5}),
            _run("b", {"acc": float("inf")}),
        ],
        "acc",
    )
    assert list(data.frame["run_id"]) == ["a"]
    assert data.omitted == 1


def _frame(data) -> pd.DataFrame:
    return data.frame.set_index("run_id")


def test_pareto_minimize_minimize_dominance_and_ties() -> None:
    data = pareto_points(
        [
            _run("a", {"x": 1, "y": 1}),
            _run("b", {"x": 2, "y": 2}),
            _run("c", {"x": 1, "y": 1}),  # tie with a
            _run("d", {"x": 3, "y": 0.5}),
        ],
        "x",
        "y",
        direction_x="minimize",
        direction_y="minimize",
    )

    frame = _frame(data)
    assert set(frame[frame["is_pareto"]].index) == {"a", "c", "d"}
    assert data.omitted == 0


def test_pareto_maximize_maximize_flips_direction() -> None:
    data = pareto_points(
        [
            _run("a", {"x": 1, "y": 1}),
            _run("b", {"x": 2, "y": 2}),
            _run("c", {"x": 3, "y": 3}),
        ],
        "x",
        "y",
        direction_x="maximize",
        direction_y="maximize",
    )

    frame = _frame(data)
    assert set(frame[frame["is_pareto"]].index) == {"c"}


def test_pareto_mixed_directions() -> None:
    data = pareto_points(
        [
            _run("a", {"x": 1, "y": 1}),
            _run("b", {"x": 2, "y": 0}),
        ],
        "x",
        "y",
        direction_x="minimize",
        direction_y="maximize",
    )

    frame = _frame(data)
    assert set(frame[frame["is_pareto"]].index) == {"a"}


def test_pareto_excludes_missing_nan_and_infinity() -> None:
    data = pareto_points(
        [
            _run("a", {"x": 1, "y": 1}),
            _run("b", {"x": float("nan"), "y": 1}),
            _run("c", {"x": 1, "y": float("inf")}),
            _run("d", {"x": 1}),
        ],
        "x",
        "y",
    )

    frame = _frame(data)
    assert list(frame.index) == ["a"]
    assert data.omitted == 3


def test_pareto_negative_values_and_empty_results() -> None:
    data = pareto_points(
        [
            _run("a", {"x": -1, "y": -2}),
            _run("b", {"x": -2, "y": -1}),
            _run("c", {"x": -1.5, "y": -1.5}),
        ],
        "x",
        "y",
    )
    frame = _frame(data)
    assert set(frame[frame["is_pareto"]].index) == {"a", "b", "c"}

    empty = pareto_points([], "x", "y")
    assert empty.frame.empty
    assert empty.omitted == 0


def test_pareto_includes_group_metadata() -> None:
    data = pareto_points(
        [
            _run("a", {"x": 1, "y": 1}, params={"protocol": "v1"}),
            _run("b", {"x": 2, "y": 2}, params={"protocol": "v2"}),
        ],
        "x",
        "y",
        group_field="protocol",
    )
    assert list(data.frame["protocol"]) == ["v1", "v2"]


def test_metric_direction_reads_selected_record_declaration(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "root",
        config=config,
        activate=False,
        metrics=[{"name": "loss", "direction": "minimize"}],
    )
    snapshot = build_snapshot(config, record_id="root")
    assert metric_direction(snapshot, "loss") == "minimize"
    assert metric_direction(snapshot, "unknown") is None
