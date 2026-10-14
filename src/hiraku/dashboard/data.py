"""Plain-Python data shaping for the dashboard.

Nothing here imports marimo, so the tree, run numbering, and run text can be
tested without a notebook runtime.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..config import HirakuConfig
from ..io import dump_yaml
from ..record import Record, list_records
from ..run import Run
from ..tracking import select_backend

if TYPE_CHECKING:
    import pandas as pd

_LAST = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ProjectRuns:
    """Records, their attributed runs, and per-source failures."""

    records: list[Record]
    runs: list[Run]
    sources: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def load(config: HirakuConfig) -> ProjectRuns:
    """Read all records and query each distinct tracking source once."""
    records = list_records(config=config)
    by_source: dict[str, Record] = {}
    for record in records:
        values = record.resolve(config=config).values
        specification = values.get("tracking_backend") or config.tracking_backend
        key = repr((specification, values.get("mlflow_experiment")))
        by_source.setdefault(key, record)

    known = {record.id for record in records}
    runs: dict[tuple[str, str], Run] = {}
    sources: dict[str, str] = {}
    errors: list[str] = []
    for record in by_source.values():
        try:
            source = select_backend(record, config).backend_name
            found = record.runs(config=config)
        except Exception as exc:
            errors.append(f"Tracking source for {record.id!r} unavailable: {exc}")
            continue
        for run in found:
            if run.record_id in known:
                runs.setdefault((source, run.id), run)
                sources[run.id] = source
    return ProjectRuns(records, list(runs.values()), sources, errors)


def children(records: Iterable[Record]) -> dict[str | None, list[str]]:
    """Map each parent ID (``None`` for roots) to its sorted child IDs."""
    records = list(records)
    known = {record.id for record in records}
    tree: dict[str | None, list[str]] = {}
    for record in records:
        parent = record.parent_id if record.parent_id in known else None
        tree.setdefault(parent, []).append(record.id)
    for ids in tree.values():
        ids.sort()
    return tree


def subtree_ids(records: Iterable[Record], record_id: str) -> set[str]:
    """Return ``record_id`` and all of its descendants."""
    tree = children(records)
    found: set[str] = set()
    stack = [record_id]
    while stack:
        current = stack.pop()
        if current not in found:
            found.add(current)
            stack.extend(tree.get(current, ()))
    return found


def record_paths(records: Iterable[Record]) -> dict[str, str]:
    """Map each record ID to its ancestry, such as ``root/a/a1``.

    Records whose parent is missing are roots, as in :func:`children`. Paths
    are built with a loop and reused for shared ancestors, so depth is unbounded.
    """
    by_id = {record.id: record for record in records}
    paths: dict[str, str] = {}
    for record in by_id.values():
        chain: list[str] = []
        seen: set[str] = set()
        current = record.id
        while current in by_id and current not in paths and current not in seen:
            chain.append(current)
            seen.add(current)
            current = by_id[current].parent_id
        prefix = paths.get(current)
        for identifier in reversed(chain):
            prefix = identifier if prefix is None else f"{prefix}/{identifier}"
            paths[identifier] = prefix
    return paths


def execution_order(runs: Iterable[Run]) -> list[Run]:
    """Sort runs by start time; ties by ID, missing timestamps last."""
    return sorted(runs, key=lambda run: (run.timestamp or _LAST, run.id))


def metric_value(run: Run, name: str | None) -> float | None:
    """Return a finite numeric metric value, or ``None``."""
    try:
        value = float(run.metrics[name])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def metric_names(runs: Iterable[Run]) -> list[str]:
    """Return numeric metric names in first-seen order."""
    names: dict[str, None] = {}
    for run in runs:
        for name in run.metrics:
            if metric_value(run, name) is not None:
                names.setdefault(name)
    return list(names)


def default_metric(records: Sequence[Record], runs: Sequence[Run]) -> str | None:
    """Prefer the first metric a record declares, else the first one logged."""
    available = metric_names(runs)
    for record in records:
        for metric in record.metrics or ():
            if metric.name in available:
                return metric.name
    return available[0] if available else None


def tree_rows(
    records: Sequence[Record], runs: Sequence[Run], metric: str | None
) -> list[dict[str, Any]]:
    """Flatten records and their runs into indented table rows.

    ``depth`` is the indentation level for the label column, and ``metric``
    adds one column, named after the metric, with each run's value.
    """
    by_id = {record.id: record for record in records}
    runs_by_record: dict[str, list[Run]] = {}
    for run in execution_order(runs):
        runs_by_record.setdefault(str(run.record_id), []).append(run)
    tree = children(records)
    rows: list[dict[str, Any]] = []
    # An explicit stack keeps arbitrarily deep trees clear of the recursion limit.
    stack = [(root, 0) for root in reversed(tree.get(None, []))]
    while stack:
        record_id, depth = stack.pop()
        record = by_id[record_id]
        rows.append(
            _row(
                f"record:{record.id}",
                depth,
                "▾ " + record.id,
                record.type,
                None,
                record.commit,
                None,
            )
        )
        if metric:
            rows[-1][metric] = ""
        for run in runs_by_record.get(record.id, ()):
            rows.append(
                _row(
                    f"run:{run.id}",
                    depth + 1,
                    "• " + run.id[:12],
                    "run",
                    run.entrypoint,
                    run.commit,
                    run.timestamp,
                )
            )
            if metric:
                rows[-1][metric] = metric_value(run, metric)
        stack.extend((child, depth + 1) for child in reversed(tree.get(record.id, [])))
    return rows


def _row(
    key: str,
    depth: int,
    label: str,
    kind: str | None,
    entrypoint: str | None,
    commit: str | None,
    timestamp: datetime | None,
) -> dict[str, Any]:
    return {
        "key": key,
        "depth": depth,
        "Record / run": label,
        "Type": kind or "",
        "Entrypoint": entrypoint or "",
        "Commit": (commit or "")[:7],
        "Started": timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "",
    }


def plot_points(
    runs: Iterable[Run], metric: str | None
) -> tuple[list[dict[str, Any]], int]:
    """Number runs 1..N by execution order and return plottable points.

    Returns the points and how many runs lacked a numeric ``metric`` value.
    """
    points: list[dict[str, Any]] = []
    missing = 0
    for number, run in enumerate(execution_order(runs), start=1):
        value = metric_value(run, metric)
        if value is None:
            missing += 1
            continue
        points.append(
            {
                "number": number,
                "value": value,
                "run": run.id,
                "record": run.record_id,
                "started": run.timestamp.isoformat() if run.timestamp else "",
            }
        )
    return points, missing


def run_text(run: Run) -> str:
    """Render the run's provenance, parameters, and metrics as YAML."""
    return dump_yaml(
        {
            "id": run.id,
            "record": run.record_id,
            "entrypoint": run.entrypoint,
            "commit": run.commit,
            "started": run.timestamp.isoformat() if run.timestamp else None,
            "params": dict(run.params),
            "metrics": dict(run.metrics),
        }
    )


def run_rows(project: ProjectRuns) -> list[dict[str, Any]]:
    """Return one flat row per run, in execution order, for tabular analysis.

    Metrics and parameters become ``metric.<name>`` and ``param.<name>`` columns,
    and ``record_path`` places the run's record in the tree.
    """
    paths = record_paths(project.records)
    return [
        {
            "run": run.id,
            "record": run.record_id,
            "record_path": paths.get(str(run.record_id)),
            "source": project.sources.get(run.id),
            "started": run.timestamp,
            "entrypoint": run.entrypoint,
            "commit": run.commit,
            "url": run.url,
            **{f"metric.{name}": value for name, value in run.metrics.items()},
            **{f"param.{name}": value for name, value in run.params.items()},
        }
        for run in execution_order(project.runs)
    ]


def record_rows(records: Sequence[Record]) -> list[dict[str, Any]]:
    """Return one row per record in tree order, with its declared metrics."""
    paths = record_paths(records)
    by_id = {record.id: record for record in records}
    return [
        {
            "record": record.id,
            "parent": record.parent_id,
            "record_path": paths[record.id],
            "type": record.type,
            "status": record.status,
            "decision": record.decision,
            "metrics": "; ".join(
                f"{metric.name}: {metric.direction}" for metric in record.metrics or ()
            )
            or None,
            "description": record.markdown.strip() or None,
        }
        for record in sorted(by_id.values(), key=lambda record: paths[record.id])
    ]


def arrow_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Return ``rows`` as a pandas DataFrame backed by Arrow columns.

    Backends report values loosely (MLflow parameters are strings, W&B keeps
    native types), so a column Arrow cannot type consistently becomes text.
    """
    import pandas as pd
    import pyarrow as pa

    names = list(dict.fromkeys(name for row in rows for name in row))
    columns = {}
    for name in names:
        values = [row.get(name) for row in rows]
        try:
            columns[name] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            columns[name] = pa.array([None if v is None else str(v) for v in values])
    return pa.table(columns).to_pandas(types_mapper=pd.ArrowDtype)


def backend_label(source: str | None) -> str:
    """Return the link label for a tracking backend name."""
    names = {"mlflow": "MLflow", "wandb": "W&B"}
    return f"Open in {names.get(source or '', 'tracking backend')}"


def safe_url(url: str | None) -> str | None:
    """Return ``url`` only if it is a credential-free HTTP(S) link."""
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
