"""Data for the dashboard: two tables and the views built from them.

``load`` reads the records and the runs of their tracking backends. ``run_rows``
and ``record_rows`` flatten them into ``runs_df`` and ``records_df`` for the
run tree, run details, metric choice, and plots. ``file_rows`` turns the jobs of
Snakemake workflow runs into ``files_df`` for the Files view. Markdown details
use the same loaded records to retain their complete front matter. Nothing here
imports marimo, so it can be tested without a notebook runtime.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
import pyarrow as pa

from ..config import HirakuConfig
from ..io import dump_yaml
from ..record import Record, list_records
from ..run import Run

_LAST = datetime.max.replace(tzinfo=UTC)

RUN_COLUMNS = (
    "run",
    "record",
    "record_path",
    "source",
    "started",
    "entrypoint",
    "commit",
    "url",
    "jobs",
)
RECORD_COLUMNS = (
    "record",
    "parent",
    "record_path",
    "type",
    "status",
    "decision",
    "commit",
    "metrics",
    "description",
)
FILE_COLUMNS = ("key", "path", "produced_by", "used_by", "last_run")


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
        specification = record.resolve(config=config).values.get("tracking_backend")
        key = repr(specification or config.tracking_backend)
        by_source.setdefault(key, record)

    known = {record.id for record in records}
    runs: dict[tuple[str, str], Run] = {}
    sources: dict[str, str] = {}
    errors: list[str] = []
    for record in by_source.values():
        try:
            backend = record.resolve_tracking_backend(config=config)
            # Without a record ID, the source lists every attributed run.
            found = backend.list_runs()
        except Exception as exc:
            errors.append(f"Tracking source for {record.id!r} unavailable: {exc}")
            continue
        for run in found:
            if run.record_id in known:
                runs.setdefault((backend.backend_name, run.id), run)
                sources[run.id] = backend.backend_name
    return ProjectRuns(records, list(runs.values()), sources, errors)


# Building the tables


def record_paths(records: Iterable[Record]) -> dict[str, str]:
    """Map each record ID to its ancestry, such as ``root/a/a1``.

    Records whose parent is missing are roots. Paths are built with a loop and
    reused for shared ancestors, so depth is unbounded.
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


def run_rows(project: ProjectRuns) -> list[dict[str, Any]]:
    """Return one flat row per run, in execution order.

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
            "jobs": _jobs(run),
            **{f"metric.{name}": value for name, value in run.metrics.items()},
            **{f"param.{name}": value for name, value in run.params.items()},
        }
        for run in execution_order(project.runs)
    ]


def record_rows(records: Sequence[Record]) -> list[dict[str, Any]]:
    """Return one row per record in tree order, with its declared metrics."""
    paths = record_paths(records)
    # Sorting with the separator below every other character puts each record
    # right after its parent and before the parent's next sibling.
    ordered = sorted(records, key=lambda record: paths[record.id].replace("/", "\0"))
    return [
        {
            "record": record.id,
            "parent": record.parent_id,
            "record_path": paths[record.id],
            "type": record.type,
            "status": record.status,
            "decision": record.decision,
            "commit": record.commit,
            "metrics": [
                {"name": metric.name, "direction": metric.direction}
                for metric in record.metrics or ()
            ],
            "description": record.markdown.strip() or None,
        }
        for record in ordered
    ]


def _jobs(run: Run) -> list[dict[str, Any]]:
    """Return the steps ``hiraku snakemake run`` stored in a run's ``jobs`` column."""
    try:
        jobs = json.loads(run.raw.get("jobs") or "[]")
    except (TypeError, ValueError):
        return []
    return jobs if isinstance(jobs, list) else []


def _step(job: Mapping[str, Any]) -> str:
    if job.get("record"):
        return f"{job['record']} · {job['entrypoint']}"
    return f"rule {job.get('rule')}"


def file_rows(project: ProjectRuns) -> list[dict[str, Any]]:
    """Describe each workflow file by the step that produces it and its users.

    Steps come from the ``jobs`` of Snakemake workflow runs, in execution order,
    so the latest run decides the producing command. ``last_run`` is the most
    recent run in which the producing step actually ran.
    """
    rows: dict[str, dict[str, Any]] = {}
    for run in execution_order(project.runs):
        for job in _jobs(run):
            for role, paths in (("outputs", job["outputs"]), ("inputs", job["inputs"])):
                for path in paths:
                    row = rows.setdefault(
                        path,
                        {"path": path, "produced_by": None, "used_by": []},
                    )
                    if role == "outputs":
                        row.update(
                            produced_by=_step(job),
                            record=job.get("record"),
                            command=job["command"],
                        )
                        if job.get("ran"):
                            row["last_run"] = run.id
                    elif _step(job) not in row["used_by"]:
                        row["used_by"].append(_step(job))
    return [
        {"key": str(index), "last_run": None, "record": None, **row}
        for index, (_, row) in enumerate(sorted(rows.items()))
    ]


def arrow_frame(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str] = ()
) -> pd.DataFrame:
    """Return ``rows`` as a pandas DataFrame backed by Arrow columns.

    ``columns`` are always present, so an empty table keeps its schema. Each
    tracking backend reports values in its own types, so a column Arrow cannot
    type consistently becomes text.
    """
    names = list(dict.fromkeys([*columns, *(name for row in rows for name in row)]))
    arrays = {}
    for name in names:
        values = [row.get(name) for row in rows]
        try:
            arrays[name] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays[name] = pa.array([None if v is None else str(v) for v in values])
    return pa.table(arrays).to_pandas(types_mapper=pd.ArrowDtype)


# Views over the tables


def metric_values(runs_df: pd.DataFrame, metric: str | None) -> pd.Series:
    """Return the runs' ``metric`` as floats, NaN where missing or not finite."""
    column = f"metric.{metric}"
    if column not in runs_df:
        return pd.Series(math.nan, index=runs_df.index, dtype="float64")
    values = pd.to_numeric(runs_df[column], errors="coerce").astype("float64")
    return values.where(values.map(math.isfinite))


def metric_names(runs_df: pd.DataFrame) -> list[str]:
    """Return the metrics with at least one numeric value, in column order."""
    names = [
        column.removeprefix("metric.")
        for column in runs_df.columns
        if column.startswith("metric.")
    ]
    return [name for name in names if metric_values(runs_df, name).notna().any()]


def default_metric(records_df: pd.DataFrame, available: Sequence[str]) -> str | None:
    """Prefer the first metric a record declares, else the first one logged."""
    for declared in records_df["metrics"]:
        for metric in declared or ():
            if metric["name"] in available:
                return metric["name"]
    return available[0] if available else None


def within(path: str, ancestor: str) -> bool:
    """Return whether record ``path`` is ``ancestor`` or lies below it."""
    return f"{path}/".startswith(f"{ancestor}/")


def subtree(runs_df: pd.DataFrame, ancestor: str | None) -> pd.DataFrame:
    """Return the rows of ``runs_df`` whose record lies in ``ancestor``'s subtree."""
    if ancestor is None or runs_df.empty:
        return runs_df.iloc[0:0]
    return runs_df[runs_df["record_path"].map(lambda path: within(path, ancestor))]


def tree_rows(
    records_df: pd.DataFrame, runs_df: pd.DataFrame, metric: str | None
) -> list[dict[str, Any]]:
    """Interleave records (in tree order) with their runs as indented table rows.

    ``depth`` comes from ``record_path``, and ``metric`` adds one column, named
    after the metric, with each run's value.
    """
    values = metric_values(runs_df, metric).tolist()
    runs_by_record: dict[str, list[dict[str, Any]]] = {}
    for run, value in zip(_records(runs_df), values, strict=True):
        run["value"] = None if math.isnan(value) else value
        runs_by_record.setdefault(run["record"], []).append(run)

    rows: list[dict[str, Any]] = []
    for record in _records(records_df):
        depth = record["record_path"].count("/")
        rows.append(
            _row(f"record:{record['record']}", depth, "▾ " + record["record"], record)
        )
        if metric:
            rows[-1][metric] = ""
        for run in runs_by_record.get(record["record"], ()):
            rows.append(
                _row(f"run:{run['run']}", depth + 1, "• " + run["run"][:12], run)
            )
            rows[-1]["Type"] = "run"
            if metric:
                rows[-1][metric] = run["value"]
    return rows


def _row(key: str, depth: int, label: str, item: Mapping[str, Any]) -> dict[str, Any]:
    started = item.get("started")
    return {
        "key": key,
        "depth": depth,
        "Record / run": label,
        "Type": item.get("type") or "",
        "Entrypoint": item.get("entrypoint") or "",
        "Commit": (item.get("commit") or "")[:7],
        "Started": started.strftime("%Y-%m-%d %H:%M:%S") if started else "",
    }


def find_run(runs_df: pd.DataFrame, run_id: str) -> dict[str, Any] | None:
    """Return the ``runs_df`` row of ``run_id`` with missing values as ``None``."""
    return next(iter(_records(runs_df[runs_df["run"] == run_id])), None)


def selection_scope(
    key: str | None,
    current_scope: str | None,
    paths: Mapping[str, str],
    runs_df: pd.DataFrame,
) -> str | None:
    """Resolve the comparison subtree after a record or run selection."""
    kind, _, identifier = (key or "").partition(":")
    if kind == "record" and identifier in paths:
        return identifier
    selected_run = find_run(runs_df, identifier) if kind == "run" else None
    if selected_run is not None and (
        current_scope not in paths
        or not within(selected_run["record_path"], paths[current_scope])
    ):
        return selected_run["record"]
    return current_scope


def run_text(run: Mapping[str, Any]) -> str:
    """Render a ``runs_df`` row's provenance, parameters, and metrics as YAML."""

    def prefixed(prefix: str) -> dict[str, Any]:
        return {
            name.removeprefix(prefix): value
            for name, value in run.items()
            if name.startswith(prefix) and value is not None
        }

    started = run.get("started")
    return dump_yaml(
        {
            "id": run["run"],
            "record": run.get("record"),
            "entrypoint": run.get("entrypoint"),
            "commit": run.get("commit"),
            "started": started.isoformat() if started else None,
            "params": prefixed("param."),
            "metrics": prefixed("metric."),
            **(
                {
                    "steps": [
                        f"{_step(job)}: {job['command']}"
                        + ("" if job.get("ran") else " (did not run)")
                        for job in run["jobs"]
                    ]
                }
                if run.get("jobs")
                else {}
            ),
        }
    )


def plot_frame(runs_df: pd.DataFrame, metric: str | None) -> tuple[pd.DataFrame, int]:
    """Number runs 1..N in row order and keep those with a finite ``metric``.

    Returns the plot data and how many runs lacked a numeric value.
    """
    runs_df = runs_df.reset_index(drop=True)  # a subtree keeps the original index
    frame = pd.DataFrame(
        {
            "number": range(1, len(runs_df) + 1),
            "run": runs_df["run"].astype(str),
            "record": runs_df["record"].astype(str),
            "started": [
                started.isoformat() if started else ""
                for started in (row.get("started") for row in _records(runs_df))
            ],
            "value": metric_values(runs_df, metric),
        }
    )
    plotted = frame.dropna(subset=["value"]).reset_index(drop=True)
    return plotted, len(frame) - len(plotted)


def tradeoff_frame(
    runs_df: pd.DataFrame,
    x_metric: str,
    y_metric: str,
    x_objective: str,
    y_objective: str,
) -> tuple[pd.DataFrame, int]:
    """Return comparable runs with a trade-off highlight and excluded count."""
    x_values = pd.to_numeric(runs_df[f"metric.{x_metric}"], errors="coerce")
    y_values = pd.to_numeric(runs_df[f"metric.{y_metric}"], errors="coerce")
    valid = (
        x_values.notna()
        & y_values.notna()
        & x_values.map(math.isfinite)
        & y_values.map(math.isfinite)
    )
    points = pd.DataFrame(
        {
            "run": runs_df.loc[valid, "run"].astype(str).to_numpy(),
            "record": runs_df.loc[valid, "record"].astype(str).to_numpy(),
            "x": x_values.loc[valid].to_numpy(),
            "y": y_values.loc[valid].to_numpy(),
        }
    ).reset_index(drop=True)
    if points.empty:
        points["non_dominated"] = pd.Series(dtype=bool)
        return points, len(runs_df)

    x_score = points["x"] * (1 if x_objective == "minimize" else -1)
    y_score = points["y"] * (1 if y_objective == "minimize" else -1)
    ordered = points.assign(_x_score=x_score, _y_score=y_score)
    non_dominated_indices: list[int] = []
    best_y = math.inf
    for _, group in ordered.groupby("_x_score", sort=True):
        group_best_y = group["_y_score"].min()
        if group_best_y <= best_y:
            non_dominated_indices.extend(
                group.index[group["_y_score"] == group_best_y].tolist()
            )
            best_y = min(best_y, group_best_y)
    points["non_dominated"] = points.index.isin(non_dominated_indices)
    return points, len(runs_df) - len(points)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return the rows of ``frame`` as dicts of Python values, ``None`` if missing."""
    return pa.Table.from_pandas(frame, preserve_index=False).to_pylist()


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
