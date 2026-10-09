"""Pure presentation helpers for the dashboard notebook.

These functions convert :class:`~hiraku.dashboard.data.DashboardSnapshot`
into plotting/dataframe structures. They are independent of marimo so they
can be tested without a browser or notebook runtime.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

import pandas as pd

from .data import DashboardSnapshot, RunView
from .plots import metric_direction


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


def default_plot_metrics(snapshot: DashboardSnapshot) -> tuple[str | None, str | None]:
    """Prefer recorded research objectives over incidental tracking metrics.

    Preserve declaration order, with the selected record first. Metrics with
    unambiguous directions take precedence so Pareto can render immediately.
    Never infer optimization directions from metric names.
    """
    available = metric_names(snapshot)
    records = sorted(
        snapshot.records, key=lambda record: record.id != snapshot.selected_record_id
    )
    cohort = {run.record_id for run in snapshot.runs} | {snapshot.selected_record_id}
    declared = list(
        dict.fromkeys(
            metric["name"]
            for record in records
            if record.id in cohort
            for metric in record.metrics
            if metric.get("name") in available
        )
    )
    known = [name for name in declared if metric_direction(snapshot, name) is not None]
    ordered = list(dict.fromkeys([*known, *declared, *available]))
    return (ordered[0] if ordered else None, ordered[1] if len(ordered) > 1 else None)


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
        columns = ["key", "id", "record", "timestamp", "commit", "entrypoint"]
        columns.extend(f"metric.{name}" for name in metric_columns)
        columns.extend(f"param.{name}" for name in param_columns)
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for run in run_list:
        row: dict[str, object] = {
            "key": run.key,
            "id": run.id,
            "record": run.record_id,
            "timestamp": run.timestamp.isoformat() if run.timestamp else None,
            "commit": run.commit,
            "entrypoint": run.entrypoint,
        }
        row.update({f"metric.{name}": run.metrics.get(name) for name in metric_columns})
        row.update({f"param.{name}": run.params.get(name) for name in param_columns})
        rows.append(row)
    frame = pd.DataFrame(rows)
    for name in metric_columns:
        frame[f"metric.{name}"] = pd.to_numeric(
            frame[f"metric.{name}"], errors="coerce"
        )
    return frame


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

    if field == "record":
        field = "record_id"
    return sort_runs(snapshot.runs, field, ascending=ascending)


def backend_action_label(source_name: str | None) -> str:
    """Return the external action label for a tracking backend."""
    return {
        "mlflow": "Open in MLflow",
        "wandb": "Open in W&B",
    }.get(source_name or "", "Open in tracking backend")


def safe_run_url(url: str | None) -> str | None:
    """Return ``url`` only when it is safe to render as an external link."""
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url
