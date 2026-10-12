"""Research helpers shared by the bundled workflows: tasks, runs, and evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from .config import HirakuConfig
from .errors import SubmissionRejected
from .record import Record, load_record
from .run import Run


def resolve_task(config: HirakuConfig, task: str | None) -> Record:
    """Return the task record, by ID or as the nearest task above the active record.

    Raises:
        hiraku.errors.SubmissionRejected: If no task record can be resolved.
    """
    if task is not None:
        record = load_record(task, config=config)
        if record.type != "task":
            raise SubmissionRejected(f"Record {task!r} is not a task record")
        return record
    active = config.active_record_id
    if active is None:
        raise SubmissionRejected("No active record; pass the task record ID")
    current: Record | None = load_record(active, config=config)
    while current is not None:
        if current.type == "task":
            return current
        parent = current.parent_id
        current = load_record(parent, config=config) if parent else None
    raise SubmissionRejected(
        f"Active record {active!r} has no task ancestor; pass the task record ID"
    )


def comparison(
    task: Record, config: HirakuConfig, entrypoint: str | None, metric: str | None
) -> tuple[str, str, Literal["minimize", "maximize"]]:
    """Choose the task's entrypoint and declared metric with its direction.

    Each is inferred only when the task declares exactly one.

    Raises:
        hiraku.errors.SubmissionRejected: If a choice is unknown or ambiguous.
    """
    values = task.resolve(config=config).values
    entrypoints = sorted((values.get("entrypoints") or {}).keys())
    declarations = {item.name: item.direction for item in values.get("metrics") or []}
    chosen_entrypoint = _choose(entrypoint, entrypoints, "entrypoint")
    chosen_metric = _choose(metric, sorted(declarations), "metric")
    return chosen_entrypoint, chosen_metric, declarations[chosen_metric]


def find_run(task: Record, config: HirakuConfig, run_id: str) -> Run | None:
    """Return an attributed run from the task's tracking source, if visible."""
    backend = task.resolve_tracking_backend(config=config)
    return next(
        (run for run in backend.list_runs(record_id=None) if run.id == run_id), None
    )


@dataclass(frozen=True)
class Evidence:
    """Classification of a tracked run.

    Attributes:
        outcome: ``valid`` for a successful run with a finite metric, ``failed``
            for a run whose status reports a failure, ``invalid`` when the run
            cannot serve as evidence.
        message: Explanation for ``failed`` and ``invalid`` runs.
        value: Metric value of a valid run.
    """

    outcome: Literal["valid", "failed", "invalid"]
    message: str = ""
    value: float | None = None


def assess_run(
    run: Run,
    *,
    record: Record,
    entrypoint: str,
    metric: str,
    success_status: str = "finished",
) -> Evidence:
    """Check that a run is attributed evidence for an experiment record.

    The run must be attributed to the record, its bound commit, and the
    entrypoint. Its tracked ``status`` must be present; any status other than
    ``success_status`` is a failed run. A recorded ``git_state`` must be clean
    and the metric must be finite.
    """
    expected = {
        "record": (run.record_id, record.id),
        "commit": (run.commit, record.commit),
        "entrypoint": (run.entrypoint, entrypoint),
    }
    for name, (actual, wanted) in expected.items():
        if wanted is None or actual != wanted:
            return Evidence(
                "invalid", f"Run {run.id!r} has {name} {actual!r}, expected {wanted!r}"
            )
    # Backends may report absent values as empty strings.
    status = run.raw.get("status") or None
    if status is None:
        return Evidence("invalid", f"Run {run.id!r} records no execution status")
    if status != success_status:
        return Evidence("failed", f"Run {run.id!r} ended with status {status!r}")
    git_state = run.raw.get("git_state") or None
    if git_state not in (None, "clean"):
        return Evidence(
            "invalid",
            f"Run {run.id!r} ran from a {git_state} working tree, so its commit "
            "does not identify the evaluated code",
        )
    value = _finite(run.metrics.get(metric))
    if value is None:
        return Evidence("invalid", f"Run {run.id!r} has no finite {metric!r}")
    return Evidence("valid", value=value)


def improves(value: float, reference: float, direction: str) -> bool:
    """Return whether *value* is strictly better than *reference*."""
    return value < reference if direction == "minimize" else value > reference


def _choose(requested: str | None, available: list[str], label: str) -> str:
    if requested is not None:
        if requested not in available:
            raise SubmissionRejected(
                f"Unknown {label} {requested!r}; available: {', '.join(available)}"
            )
        return requested
    if len(available) == 1:
        return available[0]
    if not available:
        raise SubmissionRejected(f"The task declares no {label}")
    raise SubmissionRejected(
        f"The task declares several {label}s ({', '.join(available)}); pass {label}"
    )


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
