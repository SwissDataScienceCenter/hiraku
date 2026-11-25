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

from ..io import dump_yaml
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


def within(path: str, ancestor: str) -> bool:
    """Return whether record ``path`` is ``ancestor`` or lies below it."""
    return f"{path}/".startswith(f"{ancestor}/")


def subtree(runs_df: pd.DataFrame, ancestor: str | None) -> pd.DataFrame:
    """Return the rows of ``runs_df`` whose record lies in ``ancestor``'s subtree."""
    if ancestor is None or runs_df.empty:
        return runs_df
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
        for run in runs_by_record.pop(record["record"], ()):
            rows.append(
                _row(f"run:{run['run']}", depth + 1, "• " + run["run"][:12], run)
            )
            rows[-1]["Type"] = "run"
            if metric:
                rows[-1][metric] = run["value"]
    for remaining in runs_by_record.values():
        for run in remaining:
            rows.append(_row(f"run:{run['run']}", 0, "• " + run["run"][:12], run))
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


def selection_scope(
    key: str | None,
    current_scope: str | None,
    paths: Mapping[str, str],
    runs_df: pd.DataFrame,
) -> str | None:
    """Resolve the comparison subtree after a record or run selection."""
    if key is None:
        return None
    kind, _, identifier = key.partition(":")
    if kind == "record" and identifier in paths:
        return identifier
    selected_run = find_run(runs_df, identifier) if kind == "run" else None
    if selected_run is not None and (
        current_scope not in paths
        or not within(selected_run["record_path"], paths[current_scope])
    ):
        return selected_run["record"] if selected_run["record"] in paths else None
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
    x_objective: str,
    y_objective: str,
) -> tuple[pd.DataFrame, int]:
    """Return comparable runs with a trade-off highlight and excluded count."""
    # As floats: Arrow-backed columns hold pd.NA, which math.isfinite rejects.
    x_values = metric_values(runs_df, x_metric)
    y_values = metric_values(runs_df, y_metric)
    valid = x_values.notna() & y_values.notna()
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


# Research tree: the experiments of a task and the step each one needs next.
# Colours follow the research workflow's steps; finished experiments show
# their verdict.
RESEARCH_STATES = {
    "task": "#5461a6",
    "to implement": "#848484",
    "to launch": "#3b82f6",
    "running": "#eab308",
    "to analyze": "#8b5cf6",
    "to review": "#14b8a6",
    "fixing": "#ec4899",
    "meta review": "#d946ef",
    "needs researcher": "#f97316",
    "to triage": "#06b6d4",
    "to design follow-ups": "#90ca42",
    "supports": "#10b981",
    "refutes": "#ef4444",
    "inconclusive": "#a3a3a3",
}


def experiment_state(
    record: Record, *, has_children: bool, runs: Sequence[Mapping[str, Any]]
) -> str:
    """Return the step an experiment needs, from the evidence on its record.

    This mirrors the research workflow: a verdict and triage decision settle
    finished work, otherwise the commit and the runs at that commit say whether
    the experiment is to be implemented, launched, awaited, or analyzed.
    An experiment that waits on a review exit (a fix, a meta review, or the
    researcher) shows that first. Which code and result reviews are due comes from
    ``review_stages`` on the record; a design review is not shown, because it
    cannot be told apart from a person's approval before it starts.
    ``runs`` are rows of the run table for this record.
    """
    if getattr(record, "researcher", None):
        return "needs researcher"
    # The reviews the experiment was designed under; records from before this
    # field existed are taken as reviewed in every stage once a review ran.
    stages = getattr(record, "review_stages", None)
    if stages is None:
        stages = (
            ["design", "code", "result"] if getattr(record, "reviews", None) else []
        )
    if getattr(record, "meta_pending", None):
        return "meta review"
    if getattr(record, "fixing", None):
        return "fixing"
    verdict = getattr(record, "verdict", None)
    if verdict:
        if (
            "result" in stages
            and record.state == "analyzed"
            and getattr(record, "result_review", None) is None
        ):
            return "to review"
        triage = getattr(record, "triage", None)
        if triage is None:
            return "to triage"
        if triage == "follow-up" and not has_children:
            return "to design follow-ups"
        return verdict if verdict in RESEARCH_STATES else "inconclusive"
    if not record.commit:
        return "to implement"
    if (
        "code" in stages
        and record.state == "implemented"
        and getattr(record, "code_review", None) != record.commit
    ):
        return "to review"
    attributed = [run for run in runs if run.get("commit") == record.commit]
    if any(_has_metrics(run) for run in attributed):
        return "to analyze"
    if attributed or record.state == "launched":
        return "running"
    return "to launch"


def _has_metrics(run: Mapping[str, Any]) -> bool:
    return any(
        name.startswith("metric.") and value is not None and not _is_nan(value)
        for name, value in run.items()
    )


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def research_tree(
    records: Sequence[Record], runs_df: pd.DataFrame, task_id: str | None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lay out a task's experiment tree as node and edge tables.

    The task sits at the top and each record below its parent. Leaves get
    consecutive ``x`` positions in tree order and a parent sits above the middle
    of its children. Nodes carry ``id``, ``x``, ``y``, ``state`` and
    ``summary``, ``commit``, ``disputed`` (a meta review ruled on it) and a
    ``note`` (the acceptance outcome of the task, or a failed review and the
    limitations of an experiment);
    edges carry the coordinates of both ends.
    """
    node_columns = ["id", "x", "y", "state", "summary", "commit", "disputed", "note"]
    edge_columns = ["x", "y", "x2", "y2"]
    by_id = {record.id: record for record in records}
    if task_id not in by_id:
        return pd.DataFrame([], columns=node_columns), pd.DataFrame(
            [], columns=edge_columns
        )
    children: dict[str, list[Record]] = {}
    for record in sorted(
        records, key=lambda item: (int(getattr(item, "sequence", 0) or 0), item.id)
    ):
        if record.parent_id and record.id != task_id:
            children.setdefault(record.parent_id, []).append(record)
    runs_by_record: dict[str, list[dict[str, Any]]] = {}
    for run in _records(runs_df):
        runs_by_record.setdefault(str(run.get("record")), []).append(run)

    position: dict[str, tuple[float, int]] = {}
    leaves = [0]

    def place(record_id: str, depth: int, seen: frozenset[str]) -> None:
        # ``seen`` keeps a cycle among records from looping forever.
        kids = [
            kid
            for kid in children.get(record_id, [])
            if kid.id not in seen | {record_id}
        ]
        for kid in kids:
            place(kid.id, depth + 1, seen | {record_id})
        if kids:
            x = (position[kids[0].id][0] + position[kids[-1].id][0]) / 2
        else:
            x = float(leaves[0])
            leaves[0] += 1
        position[record_id] = (x, depth)

    place(task_id, 0, frozenset())
    nodes, edges = [], []
    for record_id, (x, depth) in position.items():
        record = by_id[record_id]
        if record_id == task_id:
            state = "task"
        else:
            state = experiment_state(
                record,
                has_children=bool(children.get(record_id)),
                runs=runs_by_record.get(record_id, []),
            )
        nodes.append(
            {
                "id": record_id,
                "x": x,
                "y": depth,
                "state": state,
                "summary": str(getattr(record, "summary", None) or record.title or ""),
                "commit": (record.commit or "")[:7],
                "disputed": bool(getattr(record, "disputed", False)),
                "note": _research_note(record),
            }
        )
        if record.parent_id in position and record_id != task_id:
            px, pdepth = position[record.parent_id]
            edges.append({"x": px, "y": pdepth, "x2": x, "y2": depth})
    return pd.DataFrame(nodes, columns=node_columns), pd.DataFrame(
        edges, columns=edge_columns
    )


def _research_note(record: Record) -> str:
    acceptance = getattr(record, "acceptance", None)
    if record.type == "task":
        return f"Acceptance: {acceptance['outcome']}" if acceptance else ""
    notes = []
    failed = getattr(record, "review_failed", None)
    if failed:
        notes.append(f"Review failed ({failed['stage']})")
    limitations = getattr(record, "limitations", None) or []
    if limitations:
        notes.append(f"{len(limitations)} limitation(s)")
    return "; ".join(notes)


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
