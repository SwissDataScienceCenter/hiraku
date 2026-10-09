"""Pure plot transformations for the dashboard.

Functions in this module convert cached dashboard snapshots into dataframes
for progress and Pareto plots. They never query a tracking backend and never
mutate project files.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .data import DashboardSnapshot, RunView, sort_runs


@dataclass(frozen=True)
class PlotData:
    """A dataframe plus the number of omitted runs and why."""

    frame: pd.DataFrame
    omitted: int
    omitted_reason: str


@dataclass(frozen=True)
class ParetoData:
    """A Pareto dataframe with a frontier flag for each included run."""

    frame: pd.DataFrame
    omitted: int
    omitted_reason: str


def _is_finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _numeric(value: Any) -> float:
    number = float(value)
    assert math.isfinite(number)
    return number


def metric_direction(snapshot: DashboardSnapshot, metric: str) -> str | None:
    """Return the declared direction for a metric on the selected record."""
    for record in snapshot.records:
        if record.id != snapshot.selected_record_id:
            continue
        for item in record.metrics:
            if item.get("name") == metric and item.get("direction") in {
                "minimize",
                "maximize",
            }:
                return str(item["direction"])
    return None


def progress_points(
    runs: Sequence[RunView],
    metric: str,
    *,
    direction: str | None = None,
) -> PlotData:
    """Return one summary metric per run for a progress plot.

    Runs are ordered by timestamp and then by run key. When every run has a
    timestamp the x axis uses timestamps; otherwise a clearly labelled
    deterministic run index is used so missing timestamps never become
    fabricated time values.
    """
    ordered = sort_runs(runs, "timestamp")
    use_timestamp = bool(ordered) and all(run.timestamp is not None for run in ordered)
    rows: list[dict[str, Any]] = []
    omitted = 0
    for index, run in enumerate(ordered):
        raw = run.metrics.get(metric)
        if not _is_finite(raw):
            omitted += 1
            continue
        rows.append(
            {
                "run_id": run.id,
                "record_id": run.record_id,
                "commit": run.commit,
                "x": run.timestamp if use_timestamp else index,
                "x_kind": "timestamp" if use_timestamp else "run_index",
                "metric": metric,
                "value": _numeric(raw),
            }
        )
    frame = pd.DataFrame(rows)
    if direction in {"minimize", "maximize"} and not frame.empty:
        best = (
            frame["value"].cummin()
            if direction == "minimize"
            else frame["value"].cummax()
        )
        frame["best_so_far"] = best
    return PlotData(
        frame=frame,
        omitted=omitted,
        omitted_reason="missing or non-finite metric values",
    )


def pareto_points(
    runs: Sequence[RunView],
    metric_x: str,
    metric_y: str,
    *,
    direction_x: str = "minimize",
    direction_y: str = "minimize",
    group_field: str | None = None,
) -> ParetoData:
    """Return a two-objective scatter with a Pareto frontier flag.

    The frontier is computed over the selected comparison cohort after
    excluding missing/non-finite values. All nondominated ties are preserved.
    ``direction_*`` must be ``minimize`` or ``maximize``.
    """
    if direction_x not in {"minimize", "maximize"}:
        raise ValueError("direction_x must be 'minimize' or 'maximize'")
    if direction_y not in {"minimize", "maximize"}:
        raise ValueError("direction_y must be 'minimize' or 'maximize'")
    sign_x = -1.0 if direction_x == "maximize" else 1.0
    sign_y = -1.0 if direction_y == "maximize" else 1.0
    included: list[RunView] = []
    omitted = 0
    for run in runs:
        raw_x = run.metrics.get(metric_x)
        raw_y = run.metrics.get(metric_y)
        if not _is_finite(raw_x) or not _is_finite(raw_y):
            omitted += 1
            continue
        included.append(run)
    if not included:
        return ParetoData(
            frame=pd.DataFrame(
                columns=[
                    "run_id",
                    "record_id",
                    "commit",
                    "x",
                    "y",
                    "x_original",
                    "y_original",
                    "is_pareto",
                ]
            ),
            omitted=omitted,
            omitted_reason="missing or non-finite metric values",
        )

    scored = [
        (
            _numeric(run.metrics[metric_x]) * sign_x,
            _numeric(run.metrics[metric_y]) * sign_y,
            _numeric(run.metrics[metric_x]),
            _numeric(run.metrics[metric_y]),
            run,
        )
        for run in included
    ]
    scored.sort(key=lambda item: (item[0], item[1], item[4].key))

    best_y_previous_x = math.inf
    current_x: float | None = None
    current_min_y = math.inf
    flags: list[bool] = []
    for x, y, _, _, _ in scored:
        if current_x is None or x != current_x:
            if current_x is not None:
                best_y_previous_x = min(best_y_previous_x, current_min_y)
            current_x = x
            current_min_y = math.inf
        dominated = best_y_previous_x <= y or current_min_y < y
        flags.append(not dominated)
        current_min_y = min(current_min_y, y)

    frame = pd.DataFrame(
        [
            {
                "run_id": run.id,
                "record_id": run.record_id,
                "commit": run.commit,
                "x": x,
                "y": y,
                "x_original": original_x,
                "y_original": original_y,
                "is_pareto": is_pareto,
                **({group_field: run.params.get(group_field)} if group_field else {}),
            }
            for (x, y, original_x, original_y, run), is_pareto in zip(
                scored, flags, strict=True
            )
        ]
    )
    return ParetoData(
        frame=frame,
        omitted=omitted,
        omitted_reason="missing or non-finite metric values",
    )
