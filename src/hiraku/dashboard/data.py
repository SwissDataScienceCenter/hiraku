"""Read-only presentation snapshot for the Hiraku dashboard.

Every function in this module reads records and tracking runs without
mutating project files. Unlike :func:`hiraku.record.list_records`, the record
index built here never invokes reparent recovery and never writes to disk.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import HirakuConfig
from ..errors import RecordNotFoundError, ValidationError
from ..record import _LOCAL_FIELDS, Record
from ..run import Run
from ..tracking import MLflowBackend, backend_for

SNAPSHOT_VERSION = 2

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
    """Identity, coverage, and scope of one queried tracking source."""

    name: str
    identity: str
    label: str
    record_id: str | None
    scope: str
    status: str = "complete"
    message: str | None = None
    run_count: int | None = None


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
    sources: tuple[SourceInfo, ...]
    errors: tuple[DashboardError, ...]
    refreshed_at: datetime
    stale: bool = False


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
    paths = sorted(config.records_dir.glob("*/*.md"))
    fingerprints = {}
    for path in paths:
        try:
            fingerprints[path] = path.stat()
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
    try:
        changed = paths != sorted(config.records_dir.glob("*/*.md")) or any(
            (before.st_ino, before.st_mtime_ns, before.st_size)
            != (after.st_ino, after.st_mtime_ns, after.st_size)
            for path, before in fingerprints.items()
            for after in [path.stat()]
        )
    except OSError:
        changed = True
    if changed:
        errors.append(DashboardError("transient", "Records changed during the read"))
    return index, errors


def _resolve_effective(
    record: Record,
    parent: tuple[dict[str, Any], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge one record with its already resolved parent."""
    values = {k: v for k, v in parent[0].items() if k not in _LOCAL_FIELDS}
    sources = {k: v for k, v in parent[1].items() if k not in _LOCAL_FIELDS}
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
    resolved: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for record_id in sorted(index):
        chain: list[str] = []
        seen: set[str] = set()
        current = record_id
        while current is not None and current not in resolved:
            if current in seen or current not in index:
                problem = "parent cycle" if current in seen else "missing parent"
                errors.append(
                    DashboardError(
                        "record",
                        f"Record {record_id!r}: {problem} {current!r}",
                        record_id,
                    )
                )
                break
            chain.append(current)
            seen.add(current)
            current = index[current].parent_id
        parent = resolved.get(current, ({}, {}))
        for identifier in reversed(chain):
            parent = _resolve_effective(index[identifier], parent)
            resolved[identifier] = parent
    return resolved


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
        uri = getattr(backend, "tracking_uri", "")
        fingerprint = sha256(uri.encode()).hexdigest()
        ui_url = getattr(backend, "ui_url", None) or ""
        ui_fingerprint = sha256(ui_url.encode()).hexdigest() if ui_url else ""
        identity = (
            f"mlflow:{fingerprint}:experiment={experiment or ''}:ui={ui_fingerprint}"
        )
        label = f"MLflow {experiment}" if experiment else "MLflow"
    elif name == "wandb":
        entity = getattr(backend, "entity", None) or ""
        project = getattr(backend, "project", None) or ""
        identity = f"wandb:entity={entity}:project={project}"
        label = f"W&B {entity}/{project}".strip()
    else:
        attributes = sorted(
            (str(key), repr(value))
            for key, value in vars(backend).items()
            if key != "config"
        )
        fingerprint = sha256(repr(attributes).encode()).hexdigest()[:16]
        identity = f"{name}:{fingerprint}"
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


def _subtree_members(index: Mapping[str, Record], selected: str) -> tuple[str, ...]:
    """Return ``selected`` and its descendants, terminating on cycles."""
    children: dict[str, list[str]] = {record_id: [] for record_id in index}
    for record in index.values():
        if record.parent_id in index:
            children[record.parent_id].append(record.id)
    for values in children.values():
        values.sort()
    members: list[str] = []
    visited = {selected}
    stack = [selected]
    while stack:
        record_id = stack.pop()
        members.append(record_id)
        for child_id in children.get(record_id, ()):
            if child_id not in visited:
                visited.add(child_id)
                stack.append(child_id)
    return tuple(members)


def _backend_for_values(config: HirakuConfig, values: Mapping[str, Any]) -> Any:
    """Construct the effective backend for one resolved record."""
    specification = values.get("tracking_backend") or config.tracking_backend
    backend = backend_for(specification, config)
    if isinstance(backend, MLflowBackend):
        backend.experiment_name = backend.experiment_name or values.get(
            "mlflow_experiment"
        )
    return backend


def _source_options(backend: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """Return read options, forcing MLflow SQLite reads to a snapshot."""
    return {
        **_backend_options(backend, values),
        **({"read_only": True} if isinstance(backend, MLflowBackend) else {}),
    }


def _load_source_runs(
    backend: Any,
    *,
    source: SourceInfo,
    values: Mapping[str, Any],
    member_ids: set[str],
    _source_cache: dict[str, tuple[RunView, ...]] | None,
) -> tuple[SourceInfo, tuple[RunView, ...]]:
    """Load and retain one source, querying it at most once per cache."""
    cached = (_source_cache or {}).get(source.identity)
    if cached is None:
        backend_runs = _list_source_runs(
            backend,
            record_id=None,
            options=_source_options(backend, values),
        )
        cached = tuple(_run_view(run, source.identity) for run in backend_runs)
        if _source_cache is not None:
            _source_cache[source.identity] = cached
    retained = tuple(run for run in cached if run.record_id in member_ids)
    return replace(source, run_count=len(retained)), retained


def build_snapshot(
    config: HirakuConfig,
    *,
    record_id: str | None = None,
    scope: str = "record",
    refresh_time: datetime | None = None,
    use_active_record: bool = True,
    _record_data: tuple[dict[str, Record], list[DashboardError]] | None = None,
    _source_cache: dict[str, tuple[RunView, ...]] | None = None,
) -> DashboardSnapshot:
    """Build one versioned snapshot from the current project state.

    Records are read once and inheritance is resolved against that in-memory
    index. The selected tracking source is queried at most once; callers are
    expected to sort, filter, and chart the returned cached snapshot.

    ``record_id`` always wins. When it is ``None``, ``use_active_record=True``
    (the default) falls back to the persisted active record, while
    ``use_active_record=False`` means "no selection": records are shown and
    runs are omitted until the user selects one.
    """
    if scope not in {"record", "source", "subtree"}:
        raise ValidationError(
            "Dashboard scope must be 'record', 'source', or 'subtree'"
        )
    errors: list[DashboardError] = []
    index, index_errors = _record_data or read_record_index(config)
    # Concurrent edits can make a file disappear between glob and read. Retry
    # the read once, then report whatever remains instead of looping.
    if any(error.kind == "transient" for error in index_errors):
        index, index_errors = read_record_index(config)
    errors.extend(index_errors)
    resolved = _resolve_all(index, errors)
    records = _record_views(index, resolved, errors)

    selected = record_id
    if selected is None and use_active_record:
        selected = config.active_record_id
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
    sources: list[SourceInfo] = []
    runs: list[RunView] = []
    seen_run_keys: set[str] = set()
    if selected is not None:
        if scope == "subtree":
            members = _subtree_members(index, selected)
            member_ids = set(members)
        elif scope == "source":
            members = (selected,)
            member_ids = set(index)
        else:
            members = (selected,)
            member_ids = {selected}

        grouped: dict[str, dict[str, Any]] = {}
        selected_source_info: SourceInfo | None = None
        for member in members:
            values = resolved[member][0]
            try:
                backend = _backend_for_values(config, values)
                info = _source_info(backend, config, member, scope)
                entry = grouped.setdefault(
                    info.identity,
                    {"backend": backend, "member_ids": set(), "info": info},
                )
                entry["member_ids"].add(member)
                if member == selected:
                    selected_source_info = info
            except Exception as exc:
                errors.append(
                    DashboardError(
                        "backend",
                        f"Tracking source unavailable for record {member!r} "
                        f"({type(exc).__name__}); check backend configuration "
                        "and storage.",
                        record_id=member,
                    )
                )

        for entry in grouped.values():
            backend = entry["backend"]
            source_member_ids: set[str] = entry["member_ids"]
            info: SourceInfo = entry["info"]
            member_values = resolved[next(iter(source_member_ids))][0]
            try:
                retained_member_ids = (
                    member_ids if scope == "source" else source_member_ids
                )
                info_with_count, retained = _load_source_runs(
                    backend,
                    source=info,
                    values=member_values,
                    member_ids=retained_member_ids,
                    _source_cache=_source_cache,
                )
                sources.append(info_with_count)
                for run in retained:
                    if run.key not in seen_run_keys:
                        seen_run_keys.add(run.key)
                        runs.append(run)
                if scope != "subtree" or (
                    selected_source_info is not None
                    and info.identity == selected_source_info.identity
                ):
                    source = info_with_count
            except Exception as exc:
                errors.append(
                    DashboardError(
                        "backend",
                        f"Tracking source unavailable ({type(exc).__name__}); "
                        "check backend configuration and storage.",
                        record_id=info.record_id,
                    )
                )

    return DashboardSnapshot(
        version=SNAPSHOT_VERSION,
        project_root=config.root,
        active_record_id=config.active_record_id,
        selected_record_id=selected,
        scope=scope,
        records=records,
        runs=tuple(runs),
        source=source,
        sources=tuple(sources),
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

    def value_for(run: RunView) -> Any:
        if field.startswith("metric."):
            return run.metrics.get(field.removeprefix("metric."))
        if field.startswith("param."):
            return run.params.get(field.removeprefix("param."))
        return run.metrics.get(field, run.params.get(field))

    def numeric(run: RunView) -> float | None:
        value = value_for(run)
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    ranked = [run for run in result if numeric(run) is not None]
    unranked = [run for run in result if numeric(run) is None]
    ranked.sort(key=lambda run: run.key)
    ranked.sort(key=lambda run: numeric(run), reverse=not ascending)  # type: ignore[arg-type]
    text = [
        run
        for run in unranked
        if isinstance(value_for(run), str) and not _nonfinite(value_for(run))
    ]
    text.sort(key=lambda run: run.key)
    text.sort(key=lambda run: str(value_for(run)), reverse=not ascending)
    text_keys = {run.key for run in text}
    missing = [run for run in unranked if run.key not in text_keys]
    return [*ranked, *text, *sorted(missing, key=lambda run: run.key)]


def _nonfinite(value: str) -> bool:
    try:
        return not math.isfinite(float(value))
    except ValueError:
        return False
