"""Read-only presentation snapshot for the Hiraku dashboard.

Every function in this module reads records and tracking runs without
mutating project files. Unlike :func:`hiraku.record.list_records`, the record
index built here never invokes reparent recovery and never writes to disk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import HirakuConfig
from ..errors import RecordNotFoundError, ValidationError
from ..record import _LOCAL_FIELDS, Record
from ..run import Run
from ..tracking import MLflowBackend, backend_for

SNAPSHOT_VERSION = 1

# Display metadata allowlist for tracking backend configuration. Credentials
# such as W&B API keys and remote tracking URIs are never exposed.
_DISPLAY_BACKEND_KEYS = frozenset(
    {"type", "path", "experiment_name", "entity", "project"}
)


@dataclass(frozen=True)
class DashboardError:
    """A structured, non-fatal problem found while building a snapshot."""

    kind: str
    message: str
    record_id: str | None = None


@dataclass(frozen=True)
class RecordView:
    """Display metadata for one research record."""

    id: str
    parent_id: str | None
    depth: int
    type: str | None
    status: str | None
    decision: str | None
    commit: str | None
    branch: str | None
    markdown: str
    fields: dict[str, Any]
    sources: dict[str, Any]
    metrics: list[dict[str, str]]
    tracking_backend: str | dict[str, Any] | None
    has_runs: bool = False


@dataclass(frozen=True)
class RunView:
    """Display metadata for one normalized tracked run."""

    key: str
    id: str
    record_id: str | None
    timestamp: datetime | None
    commit: str | None
    entrypoint: str | None
    url: str | None
    metrics: dict[str, Any]
    params: dict[str, Any]
    source: str


@dataclass(frozen=True)
class SourceInfo:
    """Identity and scope of the selected tracking source."""

    name: str
    identity: str
    label: str
    record_id: str | None
    scope: str


@dataclass(frozen=True)
class DashboardSnapshot:
    """Versioned, plain-Python presentation snapshot."""

    version: int
    project_root: Path
    active_record_id: str | None
    selected_record_id: str | None
    scope: str
    records: tuple[RecordView, ...]
    runs: tuple[RunView, ...]
    source: SourceInfo | None
    errors: tuple[DashboardError, ...]
    refreshed_at: datetime


def _display_value(value: Any) -> Any:
    """Convert values to plain, serializable display structures."""
    if isinstance(value, Mapping):
        return {str(key): _display_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_display_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sanitize_backend_spec(value: Any) -> str | dict[str, Any] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _display_value(item)
            for key, item in value.items()
            if key in _DISPLAY_BACKEND_KEYS
        }
    return None


def _sanitize_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in values.items():
        if key == "tracking_backend":
            fields[key] = _sanitize_backend_spec(value)
        else:
            fields[key] = _display_value(value)
    return fields


def read_record_index(
    config: HirakuConfig,
) -> tuple[dict[str, Record], list[DashboardError]]:
    """Load records without mutating the project.

    Unlike ``hiraku.record._record_index``, this path does not call reparent
    recovery and does not unlink files. Malformed files and duplicate IDs are
    reported as structured errors instead of aborting the whole snapshot.
    """
    errors: list[DashboardError] = []
    journal = config.hiraku_dir / ".reparent-transaction.json"
    if journal.exists():
        errors.append(
            DashboardError(
                "journal",
                f"Pending reparent recovery journal exists at {journal}; "
                "the dashboard will not modify it.",
            )
        )
    index: dict[str, Record] = {}
    if not config.records_dir.is_dir():
        return index, errors
    for path in sorted(config.records_dir.glob("*/*.md")):
        try:
            record = Record.load(path)
        except OSError as exc:
            errors.append(
                DashboardError(
                    "transient",
                    f"Cannot read record {path}: {exc}",
                )
            )
            continue
        except Exception as exc:
            errors.append(
                DashboardError(
                    "record",
                    f"Cannot load record {path}: {exc}",
                )
            )
            continue
        if record.id in index:
            errors.append(
                DashboardError(
                    "record",
                    f"Duplicate record ID {record.id!r} at {path}; "
                    f"keeping {index[record.id].path}",
                    record_id=record.id,
                )
            )
            continue
        index[record.id] = record
    return index, errors


def _resolve_effective(
    record_id: str,
    index: Mapping[str, Record],
    seen: frozenset[str],
    errors: list[DashboardError],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve inheritance for one record without validating or writing."""
    record = index[record_id]
    if record_id in seen:
        errors.append(
            DashboardError(
                "record",
                f"Record parent cycle at {record_id!r}",
                record_id=record_id,
            )
        )
        return {}, {}
    values: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    if record.parent_id is not None:
        if record.parent_id not in index:
            errors.append(
                DashboardError(
                    "record",
                    f"Parent record {record.parent_id!r} for {record_id!r} "
                    "is missing or malformed",
                    record_id=record_id,
                )
            )
        else:
            parent_values, parent_sources = _resolve_effective(
                record.parent_id, index, seen | {record_id}, errors
            )
            values = {
                key: value
                for key, value in parent_values.items()
                if key not in _LOCAL_FIELDS
            }
            sources = {
                key: value
                for key, value in parent_sources.items()
                if key not in _LOCAL_FIELDS
            }
    for key, value in record.to_dict().items():
        if key == "entrypoints" and isinstance(value, Mapping):
            values[key] = {**(values.get(key) or {}), **value}
            sources[key] = {
                **(sources.get(key) or {}),
                **dict.fromkeys(value, record.id),
            }
        elif key == "metrics" and isinstance(value, list):
            values[key] = list(value)
            sources[key] = record.id
        else:
            values[key] = value
            sources[key] = record.id
    return values, sources


def _resolve_all(
    index: Mapping[str, Record], errors: list[DashboardError]
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {
        record_id: _resolve_effective(record_id, index, frozenset(), errors)
        for record_id in sorted(index)
    }


def _depth(record_id: str, index: Mapping[str, Record]) -> int:
    depth = 0
    seen: set[str] = set()
    current = index[record_id]
    while current.parent_id is not None and current.parent_id in index:
        if current.parent_id in seen:
            return depth
        seen.add(current.parent_id)
        depth += 1
        current = index[current.parent_id]
    return depth


def _record_views(
    index: Mapping[str, Record],
    resolved: Mapping[str, tuple[dict[str, Any], dict[str, Any]]],
    errors: list[DashboardError],
) -> tuple[RecordView, ...]:
    views: list[RecordView] = []
    for record_id in sorted(index):
        record = index[record_id]
        values, sources = resolved[record_id]
        fields = _sanitize_fields(values)
        views.append(
            RecordView(
                id=record.id,
                parent_id=record.parent_id,
                depth=_depth(record.id, index),
                type=record.type,
                status=record.status,
                decision=record.decision,
                commit=record.commit,
                branch=record.branch,
                markdown=record.markdown,
                fields=fields,
                sources=_display_value(sources),
                metrics=[
                    {
                        "name": str(item.get("name", "")),
                        "direction": str(item.get("direction", "")),
                    }
                    for item in values.get("metrics") or []
                ],
                tracking_backend=fields.get("tracking_backend"),
            )
        )
    return tuple(views)


def _source_info(
    backend: Any, config: HirakuConfig, record_id: str, scope: str
) -> SourceInfo:
    name = backend.backend_name
    if name == "csv":
        path = getattr(backend, "path", None) or config.root / "runs.csv"
        identity = f"csv:{path.resolve()}"
        label = f"CSV {path}"
    elif name == "mlflow":
        experiment = getattr(backend, "experiment_name", None)
        identity = f"mlflow:experiment={experiment or ''}"
        label = f"MLflow {experiment}" if experiment else "MLflow"
    elif name == "wandb":
        entity = getattr(backend, "entity", None) or ""
        project = getattr(backend, "project", None) or ""
        identity = f"wandb:entity={entity}:project={project}"
        label = f"W&B {entity}/{project}".strip()
    else:
        identity = f"{name}:default"
        label = name
    return SourceInfo(
        name=name,
        identity=identity,
        label=label,
        record_id=record_id,
        scope=scope,
    )


def _backend_options(backend: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """Return per-backend read options without duplicating backend resolution."""
    options: dict[str, Any] = {}
    if isinstance(backend, MLflowBackend):
        experiment = backend.experiment_name or values.get("mlflow_experiment")
        if experiment:
            options["experiment_name"] = experiment
    return options


def _list_source_runs(
    backend: Any, *, record_id: str | None, options: Mapping[str, Any]
) -> list[Run]:
    """Read a source once using the backend contract available at runtime.

    ``record_id=None`` means "all attributed runs in this source". The current
    Hiraku API exposes that through ``list_all_recorded_runs``; PR 77 proposes
    ``list_runs(record_id=None)`` instead, so both are supported here.
    """
    if record_id is None:
        lister = getattr(backend, "list_all_recorded_runs", None)
        if lister is not None:
            return list(lister(**options))
        return list(backend.list_runs(record_id=None))
    return list(backend.list_runs(record_id=record_id))


def _run_view(run: Run, source: str) -> RunView:
    return RunView(
        key=f"{source}::{run.id}",
        id=run.id,
        record_id=run.record_id,
        timestamp=run.timestamp,
        commit=run.commit,
        entrypoint=run.entrypoint,
        url=run.url,
        metrics=dict(run.metrics),
        params=dict(run.params),
        source=source,
    )


def build_snapshot(
    config: HirakuConfig,
    *,
    record_id: str | None = None,
    scope: str = "record",
    refresh_time: datetime | None = None,
) -> DashboardSnapshot:
    """Build one versioned snapshot from the current project state.

    Records are read once and inheritance is resolved against that in-memory
    index. The selected tracking source is queried at most once; callers are
    expected to sort, filter, and chart the returned cached snapshot.
    """
    if scope not in {"record", "source"}:
        raise ValidationError("Dashboard scope must be 'record' or 'source'")
    errors: list[DashboardError] = []
    index, index_errors = read_record_index(config)
    # Concurrent edits can make a file disappear between glob and read. Retry
    # the read once, then report whatever remains instead of looping.
    if any(error.kind == "transient" for error in index_errors):
        index, index_errors = read_record_index(config)
    errors.extend(index_errors)
    resolved = _resolve_all(index, errors)
    records = _record_views(index, resolved, errors)

    selected = record_id if record_id is not None else config.active_record_id
    if record_id is not None and record_id not in index:
        raise RecordNotFoundError(f"Record {record_id!r} not found")
    if selected is not None and selected not in index:
        errors.append(
            DashboardError(
                "record",
                f"Selected record {selected!r} is missing or malformed",
                record_id=selected,
            )
        )
        selected = None

    source: SourceInfo | None = None
    runs: tuple[RunView, ...] = ()
    if selected is not None:
        values = resolved[selected][0]
        try:
            specification = values.get("tracking_backend") or config.tracking_backend
            backend = backend_for(specification, config)
            source = _source_info(backend, config, selected, scope)
            backend_runs = _list_source_runs(
                backend,
                record_id=selected if scope == "record" else None,
                options=_backend_options(backend, values),
            )
            runs = tuple(_run_view(run, source.identity) for run in backend_runs)
        except Exception as exc:
            errors.append(
                DashboardError(
                    "backend",
                    f"Tracking source unavailable: {exc}",
                    record_id=selected,
                )
            )

    return DashboardSnapshot(
        version=SNAPSHOT_VERSION,
        project_root=config.root,
        active_record_id=config.active_record_id,
        selected_record_id=selected,
        scope=scope,
        records=records,
        runs=runs,
        source=source,
        errors=tuple(errors),
        refreshed_at=refresh_time or datetime.now(UTC),
    )


def sort_runs(
    runs: Sequence[RunView], field: str, *, ascending: bool = True
) -> list[RunView]:
    """Sort run views deterministically for presentation.

    Numeric metric/parameter values sort numerically with missing values last;
    timestamp and string fields sort with missing values last as well. Missing
    values are never coerced to zero.
    """
    result = list(runs)
    if not result:
        return result
    direct_fields = {"id", "record_id", "commit", "entrypoint", "url", "source", "key"}
    if field in direct_fields:
        present = [run for run in result if getattr(run, field) is not None]
        missing = [run for run in result if getattr(run, field) is None]
        present.sort(key=lambda run: run.key)
        present.sort(key=lambda run: str(getattr(run, field)), reverse=not ascending)
        return [*present, *sorted(missing, key=lambda run: run.key)]
    if field == "timestamp":
        present = [run for run in result if run.timestamp is not None]
        missing = [run for run in result if run.timestamp is None]
        present.sort(key=lambda run: run.key)
        present.sort(key=lambda run: run.timestamp, reverse=not ascending)  # type: ignore[arg-type]
        return [*present, *sorted(missing, key=lambda run: run.key)]

    def numeric(run: RunView) -> float | None:
        value = run.metrics.get(field, run.params.get(field))
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    ranked = [run for run in result if numeric(run) is not None]
    unranked = [run for run in result if numeric(run) is None]
    ranked.sort(key=lambda run: run.key)
    ranked.sort(key=lambda run: numeric(run), reverse=not ascending)  # type: ignore[arg-type]
    return [*ranked, *sorted(unranked, key=lambda run: run.key)]
