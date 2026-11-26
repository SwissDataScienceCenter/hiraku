"""Data for the dashboard: two tables and the views built from them.

``run_rows`` and ``record_rows`` flatten runs and records for the run tree,
run details, metric choice, and plots. Markdown details use the same
loaded records to retain their complete front matter. Nothing here imports
marimo, so it can be tested without a notebook runtime.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
import pyarrow as pa

from ..errors import GitError
from ..git import GitRepository
from ..io import dump_yaml
from ..project import Project
from ..record import Record
from ..tracking import Run

_LAST = datetime.max.replace(tzinfo=UTC)

RUN_COLUMNS = (
    "run",
    "record",
    "record_path",
    "source",
    "started",
    "duration",
    "commit",
    "url",
)
RECORD_COLUMNS = (
    "record",
    "parent",
    "record_path",
    "type",
    "state",
    "decision",
    "commit",
    "description",
)
RUN_TABLE_COLUMNS = ("Record / run", "Type", "Commit", "Started", "Duration")
_RUN_TABLE_PROVENANCE = ("run", "record", "record_path", "source", "url")


def github_repository(project: Project) -> str | None:
    """Resolve a github.com origin to a browser URL; missing Git is optional."""
    try:
        origin = GitRepository(project.root).origin_url or ""
    except GitError:
        return None
    if origin.startswith("git@github.com:"):
        origin = "https://github.com/" + origin.removeprefix("git@github.com:")
    parsed = urlsplit(origin)
    if parsed.hostname != "github.com" or parsed.scheme not in {"https", "ssh"}:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return "https://github.com/" + "/".join(parts)


def lineage_records(
    records: Sequence[Record], *, types: Sequence[str] = (), states: Sequence[str] = ()
) -> list[Record]:
    """Filter local fields while retaining lineage ancestors, tolerating cycles."""
    by_id = {record.id: record for record in records}
    visible: set[str] = set()
    for record in records:
        if (types and record.type not in types) or (
            states and record.state not in states
        ):
            continue
        current = record
        while current is not None and current.id not in visible:
            visible.add(current.id)
            current = by_id.get(current.parent_id)
    return [record for record in records if record.id in visible]


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
            name = by_id[identifier].title or identifier.rsplit("/", 1)[-1]
            prefix = name if prefix is None else f"{prefix}/{name}"
            paths[identifier] = prefix
    return paths


def execution_order(runs: Iterable[Run]) -> list[Run]:
    """Sort runs by start time; ties by ID, missing timestamps last."""
    return sorted(runs, key=lambda run: (run.timestamp or _LAST, run.id))


def run_rows(
    runs: Iterable[Run], *, records: Iterable[Record], source: str | None
) -> list[dict[str, Any]]:
    """Return one flat row per run, in execution order.

    Metrics and parameters become ``metric.<name>`` and ``param.<name>`` columns,
    and ``record_path`` places the run's record in the tree.
    """
    paths = record_paths(records)
    return [
        {
            "run": run.id,
            "record": run.record_id,
            "record_path": paths.get(str(run.record_id)),
            "source": source,
            "started": run.timestamp,
            "duration": run.duration,
            "commit": run.commit,
            "url": run.url,
            **{f"metric.{name}": value for name, value in run.metrics.items()},
            **{f"param.{name}": value for name, value in run.params.items()},
        }
        for run in execution_order(runs)
    ]


def record_rows(records: Sequence[Record]) -> list[dict[str, Any]]:
    """Return one row per record in tree order."""
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
            "state": record.state,
            "decision": record.decision,
            "commit": record.commit,
            "description": record.markdown.strip() or None,
        }
        for record in ordered
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


def filter_leaderboard_runs(
    runs_df: pd.DataFrame,
    records: Sequence[Record],
    record_id: str | None,
    *,
    include_unattributed: bool,
) -> pd.DataFrame:
    """Filter by record ID and explicit lineage, retaining source order."""
    attributed = runs_df["record"].astype("string").fillna("").ne("")
    if record_id is None:
        matching = attributed
    else:
        ids = {record_id}
        children: dict[str | None, list[str]] = {}
        for record in records:
            children.setdefault(record.parent_id, []).append(record.id)
        pending = [record_id]
        while pending:
            for child in children.get(pending.pop(), ()):
                if child not in ids:
                    ids.add(child)
                    pending.append(child)
        matching = runs_df["record"].isin(ids)
    if include_unattributed:
        matching |= ~attributed
    return runs_df[matching]


def run_table_metric_column(metric: str | None) -> str | None:
    """Keep the metric label short unless it would overwrite another column."""
    if metric and (
        metric in (*RUN_TABLE_COLUMNS, *_RUN_TABLE_PROVENANCE, "key", "depth")
        or metric.startswith(("metric.", "param."))
    ):
        return f"metric.{metric}"
    return metric


def tree_rows(
    records_df: pd.DataFrame,
    runs_df: pd.DataFrame,
    metric: str | None,
    *,
    include_records: bool = True,
) -> list[dict[str, Any]]:
    """Interleave records (in tree order) with their runs as indented table rows.

    ``depth`` comes from ``record_path``, and ``metric`` adds one column, named
    after the metric, with each run's value. Other metrics, parameters, and
    provenance are available as additional columns.
    """
    metric_column = run_table_metric_column(metric)
    extra_columns = [
        *_RUN_TABLE_PROVENANCE,
        *(
            name
            for name in runs_df.columns
            if name.startswith(("metric.", "param.")) and name != f"metric.{metric}"
        ),
    ]

    def run_row(run: dict[str, Any], depth: int) -> dict[str, Any]:
        row = _row(f"run:{run['run']}", depth, run["run"][:12], run)
        row["Type"] = "run"
        if metric_column:
            row[metric_column] = run["value"]
        row.update({name: run.get(name) for name in extra_columns})
        return row

    values = metric_values(runs_df, metric).tolist()
    runs_by_record: dict[str, list[dict[str, Any]]] = {}
    for run, value in zip(_records(runs_df), values, strict=True):
        run["value"] = None if math.isnan(value) else value
        owner = run["record"] if include_records else None
        runs_by_record.setdefault(owner, []).append(run)

    rows: list[dict[str, Any]] = []
    for record in _records(records_df) if include_records else ():
        depth = record["record_path"].count("/")
        rows.append(
            _row(f"record:{record['record']}", depth, "▾ " + record["record"], record)
        )
        if metric_column:
            rows[-1][metric_column] = ""
        rows[-1].update(dict.fromkeys(extra_columns))
        for run in runs_by_record.pop(record["record"], ()):
            rows.append(run_row(run, depth + 1))
    for remaining in runs_by_record.values():
        for run in remaining:
            rows.append(run_row(run, 0))
    return rows


def _row(key: str, depth: int, label: str, item: Mapping[str, Any]) -> dict[str, Any]:
    started = item.get("started")
    return {
        "key": key,
        "depth": depth,
        "Record / run": label,
        "Type": item.get("type") or "",
        "Commit": (item.get("commit") or "")[:7],
        "Started": started.strftime("%Y-%m-%d %H:%M:%S") if started else "",
        "Duration": format_duration(item.get("duration")),
    }


def format_duration(seconds: float | None) -> str:
    """Render ``seconds`` as ``850 ms``, ``4.2 s``, ``3 m 07 s`` or ``1 h 05 m``."""
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 59.95:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} m {rest:02d} s"
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes:02d} m"


def find_run(runs_df: pd.DataFrame, run_id: str) -> dict[str, Any] | None:
    """Return the ``runs_df`` row of ``run_id`` with missing values as ``None``."""
    return next(iter(_records(runs_df[runs_df["run"] == run_id])), None)


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
            "commit": run.get("commit"),
            "started": started.isoformat() if started else None,
            "duration": run.get("duration"),
            "params": prefixed("param."),
            "metrics": prefixed("metric."),
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
            "duration": [
                format_duration(row.get("duration")) for row in _records(runs_df)
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
) -> pd.DataFrame:
    """Return runs with finite values for both metrics, in source order."""
    # As floats: Arrow-backed columns hold pd.NA, which math.isfinite rejects.
    x_values = metric_values(runs_df, x_metric)
    y_values = metric_values(runs_df, y_metric)
    valid = x_values.notna() & y_values.notna()
    return pd.DataFrame(
        {
            "run": runs_df.loc[valid, "run"].astype(str).to_numpy(),
            "record": runs_df.loc[valid, "record"].astype(str).to_numpy(),
            "x": x_values.loc[valid].to_numpy(),
            "y": y_values.loc[valid].to_numpy(),
        }
    ).reset_index(drop=True)


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
