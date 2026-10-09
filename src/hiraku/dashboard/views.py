"""Pure presentation helpers for the dashboard notebook.

These functions convert :class:`~hiraku.dashboard.data.DashboardSnapshot`
into plotting/dataframe structures. They are independent of marimo so they
can be tested without a browser or notebook runtime.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .data import DashboardSnapshot, RunView


def records_frame(snapshot: DashboardSnapshot) -> pd.DataFrame:
    """Return one row per record with parent and depth columns."""
    return pd.DataFrame(
        [
            {
                "id": record.id,
                "parent": record.parent_id,
                "depth": record.depth,
                "type": record.type,
                "status": record.status,
            }
            for record in snapshot.records
        ]
    )


def metric_names(snapshot: DashboardSnapshot) -> list[str]:
    """Return metric names in deterministic first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for run in snapshot.runs:
        for name in run.metrics:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def param_names(snapshot: DashboardSnapshot) -> list[str]:
    """Return parameter names in deterministic first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for run in snapshot.runs:
        for name in run.params:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def runs_frame(
    runs: Iterable[RunView],
    *,
    metrics: Iterable[str] = (),
    params: Iterable[str] = (),
) -> pd.DataFrame:
    """Return a run table with the selected metric and parameter columns."""
    run_list = list(runs)
    metric_columns = list(metrics)
    param_columns = list(params)
    if not run_list:
        columns = ["id", "record", "timestamp", "commit", "entrypoint"]
        columns.extend(f"metric.{name}" for name in metric_columns)
        columns.extend(f"param.{name}" for name in param_columns)
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for run in run_list:
        row: dict[str, object] = {
            "id": run.id,
            "record": run.record_id,
            "timestamp": run.timestamp.isoformat() if run.timestamp else None,
            "commit": run.commit,
            "entrypoint": run.entrypoint,
        }
        row.update({f"metric.{name}": run.metrics.get(name) for name in metric_columns})
        row.update({f"param.{name}": run.params.get(name) for name in param_columns})
        rows.append(row)
    return pd.DataFrame(rows)


def sort_fields(snapshot: DashboardSnapshot) -> list[str]:
    """Return fields that can sort the cached run table."""
    fields = ["timestamp", "id", "record", "commit"]
    fields.extend(f"metric.{name}" for name in metric_names(snapshot))
    fields.extend(f"param.{name}" for name in param_names(snapshot))
    return fields


def sorted_runs(
    snapshot: DashboardSnapshot, field: str, *, ascending: bool = True
) -> list[RunView]:
    """Sort cached runs without re-querying the tracking source."""
    from .data import sort_runs

    if field.startswith("metric."):
        field = field.removeprefix("metric.")
    elif field.startswith("param."):
        field = field.removeprefix("param.")
    return sort_runs(snapshot.runs, field, ascending=ascending)
