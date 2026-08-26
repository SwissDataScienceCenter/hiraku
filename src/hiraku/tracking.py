from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from hiraku.config import Project
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import (
    ahead_behind,
    ancestry_distance,
    branch_exists,
    commit_parents,
    is_ancestor,
    resolve_commit,
)
from hiraku.records import ExperimentRecord, list_experiments, read_task, resolve_task
from hiraku.util import json_dump, render_table, validate_utc_timestamp

PROVENANCE_COLUMNS = ["run_id", "task_id", "experiment_id", "commit", "created_at"]
DEFAULT_METRIC_LIMIT = 6


@dataclass
class RunResult:
    rows: list[dict[str, Any]]
    warnings: list[str]


class TrackingBackend(Protocol):
    def read_runs(
        self, task_id: str, include_all: bool = False, allow_empty: bool = False
    ) -> RunResult: ...


def add_runs_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("runs", help="Inspect tracked runs")
    command.add_argument("task_id", nargs="?")
    command.add_argument("--task")
    command.add_argument("--sort")
    directions = command.add_mutually_exclusive_group()
    directions.add_argument("--ascending", action="store_true")
    directions.add_argument("--descending", action="store_true")
    command.add_argument("--all", action="store_true")
    command.add_argument(
        "--metrics",
        help="Comma-separated metric columns for the human-readable table",
    )
    command.add_argument("--verbose", action="store_true")
    command.add_argument("--json", action="store_true")


class TableBackend:
    def __init__(self, project: Project) -> None:
        self.project = project

    def read_runs(
        self, task_id: str, include_all: bool = False, allow_empty: bool = False
    ) -> RunResult:
        if include_all:
            raise ValidationError("--all is available only with tracking_backend: mlflow.")
        read_task(self.project, task_id)
        path = self.project.runs_path(task_id)
        if not path.exists() or path.stat().st_size == 0:
            return RunResult([], [])
        return RunResult(read_table(path, task_id), [])


class MlflowBackend:
    def __init__(self, project: Project) -> None:
        self.project = project
        if not project.remote_mlflow:
            project.mlflow_tracking_db.parent.mkdir(parents=True, exist_ok=True)
        try:
            from mlflow.exceptions import MlflowException
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise HirakuError(
                "MLflow tracking requires the optional dependency; install hiraku[mlflow]."
            ) from exc
        self.mlflow_exception = MlflowException
        try:
            self.client = MlflowClient(tracking_uri=project.mlflow_tracking_uri)
        except MlflowException as exc:
            raise HirakuError(
                "Could not initialize the configured MLflow store; check connectivity and "
                "standard MLflow authentication settings."
            ) from exc

    def read_runs(
        self, task_id: str, include_all: bool = False, allow_empty: bool = False
    ) -> RunResult:
        task = read_task(self.project, task_id)
        known_experiments = {record.id for record in list_experiments(self.project, task_id)}
        if not task.mlflow_experiment:
            raise ValidationError(
                f"Task {task_id!r} requires mlflow_experiment with tracking_backend: mlflow."
            )
        try:
            experiment = self.client.get_experiment_by_name(task.mlflow_experiment)
        except self.mlflow_exception as exc:
            raise HirakuError(
                "Could not query the configured MLflow store; check connectivity and standard "
                "MLflow authentication settings."
            ) from exc
        if experiment is None:
            if allow_empty:
                return RunResult(
                    [],
                    [f"MLflow experiment {task.mlflow_experiment!r} does not exist yet."],
                )
            raise HirakuError(f"MLflow experiment {task.mlflow_experiment!r} does not exist.")
        try:
            values = self.client.search_runs([experiment.experiment_id])
        except self.mlflow_exception as exc:
            raise HirakuError(
                "Could not query runs from the configured MLflow store; check connectivity and "
                "standard MLflow authentication settings."
            ) from exc
        if not values:
            if allow_empty:
                return RunResult([], [])
            raise HirakuError(f"MLflow experiment {task.mlflow_experiment!r} contains no runs.")
        rows: list[dict[str, Any]] = []
        omitted: list[str] = []
        warnings: list[str] = []
        for run in values:
            params = dict(run.data.params)
            metrics = dict(run.data.metrics)
            tags = dict(run.data.tags)
            missing = [key for key in ("task_id", "experiment_id", "commit") if not params.get(key)]
            wrong_task = bool(params.get("task_id") and params["task_id"] != task_id)
            issues = [*(f"missing {key}" for key in missing)]
            if wrong_task:
                issues.append(f"task_id is {params['task_id']!r}")
            experiment_id = params.get("experiment_id", "")
            if experiment_id and experiment_id not in known_experiments:
                issues.append(f"unknown experiment_id {experiment_id!r}")
            commit = params.get("commit", "")
            if commit and not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
                issues.append("commit is not an exact hexadecimal SHA")
            if issues and not include_all:
                omitted.append(run.info.run_id)
                continue
            created_at = ""
            if run.info.start_time is not None:
                created_at = (
                    datetime.fromtimestamp(run.info.start_time / 1000, tz=UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
            row: dict[str, Any] = {
                "run_id": run.info.run_id,
                "task_id": params.get("task_id", ""),
                "experiment_id": params.get("experiment_id", ""),
                "commit": params.get("commit", ""),
                "created_at": created_at,
                "status": run.info.status,
                "params": params,
                "metrics": metrics,
                "tags": tags,
            }
            row.update({f"params.{key}": value for key, value in params.items()})
            row.update({f"metrics.{key}": value for key, value in metrics.items()})
            row.update({f"tags.{key}": value for key, value in tags.items()})
            if issues:
                row["_provenance_warnings"] = issues
                warnings.append(f"MLflow run {run.info.run_id}: {', '.join(issues)}.")
            rows.append(row)
        if omitted:
            warnings.append(
                f"Omitted {len(omitted)} MLflow run(s) with incomplete or wrong-task "
                "provenance; use --all to inspect them."
            )
        return RunResult(rows, warnings)


def backend_for(project: Project) -> TrackingBackend:
    if project.tracking_backend == "table":
        return TableBackend(project)
    if project.tracking_backend == "mlflow":
        return MlflowBackend(project)
    raise ValidationError(f"Unknown tracking backend {project.tracking_backend!r}.")


def read_runs(project: Project, task_id: str, include_all: bool = False) -> list[dict[str, Any]]:
    return backend_for(project).read_runs(task_id, include_all).rows


def read_table(path: Path, task_id: str) -> list[dict[str, str]]:
    delimiter = "\t" if path.suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter)
        fields = reader.fieldnames or []
        missing = [field for field in PROVENANCE_COLUMNS if field not in fields]
        if missing:
            raise ValidationError(
                f"Tracking table {path} is missing required columns: {', '.join(missing)}."
            )
        rows = [dict(row) for row in reader]
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValidationError(f"Malformed tracking row {index} in {path}.")
        if row["task_id"] != task_id:
            raise ValidationError(
                f"Tracking row {index} in {path} has task_id {row['task_id']!r}, "
                f"expected {task_id!r}."
            )
        validate_utc_timestamp(row["created_at"], f"Run created_at on row {index} in {path}")
    return rows


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def sort_runs(rows: list[dict[str, Any]], column: str, descending: bool) -> list[dict[str, Any]]:
    ranked: list[tuple[int, dict[str, Any], float]] = []
    unranked: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        value = numeric(row.get(column))
        if value is None:
            unranked.append((index, row))
        else:
            ranked.append((index, row, value))
    ranked.sort(key=lambda item: item[2], reverse=descending)
    result: list[dict[str, Any]] = []
    for rank, (_, row, _) in enumerate(ranked, start=1):
        result.append({**row, "_rank": rank})
    result.extend(dict(row) for _, row in unranked)
    return result


def _metric_field(project: Project, metric: str) -> str:
    if project.tracking_backend == "mlflow" and not metric.startswith("metrics."):
        return f"metrics.{metric}"
    return metric


def _metric_name(value: str) -> str:
    return value.removeprefix("metrics.")


def _available_metrics(project: Project, rows: list[dict[str, Any]]) -> list[str]:
    if project.tracking_backend == "mlflow":
        return sorted(
            {key for row in rows for key in row.get("metrics", {}) if isinstance(key, str)}
        )
    excluded = {*PROVENANCE_COLUMNS, "_rank", "_commit_mismatch"}
    return sorted({key for row in rows for key in row if key not in excluded})


def _parse_metrics(value: str | None) -> list[str] | None:
    if value is None:
        return None
    metrics = [metric.strip() for metric in value.split(",")]
    if not metrics or any(not metric for metric in metrics):
        raise ValidationError("--metrics must be a comma-separated list of metric names.")
    if len(set(metrics)) != len(metrics):
        raise ValidationError("--metrics must not contain duplicate metric names.")
    return metrics


def _format_number(value: Any) -> Any:
    if isinstance(value, float):
        return format(value, ".6g")
    return value


def _compact_time(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    compact = value.replace("T", " ")
    if compact.endswith("Z") and "." in compact:
        compact = compact.split(".", 1)[0] + "Z"
    return compact


def _short(value: Any, length: int) -> Any:
    return value[:length] if isinstance(value, str) and value else value


def _verbose_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    excluded = {"params", "metrics", "tags"}
    columns: list[str] = []
    for preferred in PROVENANCE_COLUMNS + ["status"]:
        if any(preferred in row for row in rows):
            columns.append(preferred)
    for row in rows:
        for key in row:
            if key not in excluded and key not in columns:
                columns.append(key)
    display: list[dict[str, Any]] = []
    for row in rows:
        value = {key: _format_number(row.get(key, "")) for key in columns}
        value["commit"] = _short(row.get("commit", ""), 10)
        value["created_at"] = _compact_time(row.get("created_at", ""))
        display.append(value)
    return display, columns


def _compact_rows(
    project: Project,
    rows: list[dict[str, Any]],
    metrics: list[str],
    ranked: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    columns = ["run", "experiment", "created_at"]
    if any(row.get("status") for row in rows):
        columns.append("status")
    columns.append("commit")
    parameter_names: list[str] = []
    if project.tracking_backend == "mlflow":
        parameter_names = sorted(
            {
                key
                for row in rows
                for key in row.get("params", {})
                if key not in {"task_id", "experiment_id", "commit"}
            }
        )
        columns.extend(parameter_names)
    columns.extend(metrics)
    if ranked:
        columns.insert(0, "rank")
    display: list[dict[str, Any]] = []
    for row in rows:
        value: dict[str, Any] = {
            "run": _short(row.get("run_id", ""), 8),
            "experiment": row.get("experiment_id", ""),
            "created_at": _compact_time(row.get("created_at", "")),
            "status": row.get("status", ""),
            "commit": _short(row.get("commit", ""), 10),
            "rank": row.get("_rank", ""),
        }
        for name in parameter_names:
            value[name] = row.get("params", {}).get(name, "")
        for metric in metrics:
            value[metric] = _format_number(row.get(_metric_field(project, metric), ""))
        display.append(value)
    return display, columns


def show_runs(project: Project, args: argparse.Namespace) -> int:
    task_id = resolve_task(project, args.task_id, args.task)
    if not args.sort and (args.ascending or args.descending):
        raise ValidationError("--ascending and --descending require --sort.")
    task = read_task(project, task_id)
    loaded = backend_for(project).read_runs(task_id, args.all)
    for warning in loaded.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    rows: list[dict[str, Any]] = annotate_commit_mismatches(
        loaded.rows, list_experiments(project, task_id)
    )
    declared = {metric["name"]: metric["direction"] for metric in task.metrics}
    sort_name = args.sort
    if sort_name is None and task.metrics:
        sort_name = task.metrics[0]["name"]
    sort_field = _metric_field(project, sort_name) if sort_name else None
    if sort_field:
        if args.sort and rows and not any(sort_field in row for row in rows):
            raise ValidationError(f"Sort field {sort_name!r} does not exist in tracked runs.")
        direction = declared.get(_metric_name(sort_name))
        if not (args.ascending or args.descending) and direction is None:
            raise ValidationError(
                f"--sort {sort_name!r} requires --ascending or --descending because the "
                "metric has no declared direction in task.md."
            )
        descending = args.descending or (not args.ascending and direction == "maximize")
        rows = sort_runs(rows, sort_field, descending)
    if args.json:
        print(json_dump(rows))
    else:
        requested_metrics = _parse_metrics(args.metrics)
        available_metrics = _available_metrics(project, rows)
        if requested_metrics is not None:
            unknown = [metric for metric in requested_metrics if metric not in available_metrics]
            if rows and unknown:
                raise ValidationError(
                    f"Unknown metric(s) for task {task_id!r}: {', '.join(unknown)}."
                )
            metrics = requested_metrics
        elif task.metrics:
            metrics = [metric["name"] for metric in task.metrics]
        else:
            metrics = available_metrics[:DEFAULT_METRIC_LIMIT]
            if len(available_metrics) > DEFAULT_METRIC_LIMIT:
                print(
                    f"Warning: showing {DEFAULT_METRIC_LIMIT} of {len(available_metrics)} "
                    "metrics; use --metrics or --verbose to select more.",
                    file=sys.stderr,
                )
        if args.sort and _metric_name(args.sort) in available_metrics:
            metric = _metric_name(args.sort)
            metrics = [metric, *(value for value in metrics if value != metric)]
        if args.verbose:
            display, columns = _verbose_rows(rows)
        else:
            display, columns = _compact_rows(project, rows, metrics, bool(sort_field))
        print(render_table(display, columns))
        mismatches = [row for row in rows if row.get("_commit_mismatch")]
        if mismatches:
            print(
                f"Warning: {len(mismatches)} run(s) do not match their experiment's bound commit.",
                file=sys.stderr,
            )
    return 0


def annotate_commit_mismatches(
    rows: list[dict[str, Any]], records: list[ExperimentRecord]
) -> list[dict[str, Any]]:
    bound = {record.id: record.data["commit"] for record in records}
    result: list[dict[str, Any]] = []
    for row in rows:
        expected = bound.get(str(row.get("experiment_id", "")), "")
        result.append(
            {
                **row,
                "_commit_mismatch": bool(expected and row.get("commit", "") != expected),
            }
        )
    return result


def _nearest_parent_experiments(
    project: Project, record: ExperimentRecord, records: list[ExperimentRecord]
) -> list[str]:
    commit = record.data["commit"]
    if not commit:
        return []
    candidates: list[tuple[int, str]] = []
    for other in records:
        parent = other.data["commit"]
        if other.id == record.id or not parent or parent == commit:
            continue
        distance = ancestry_distance(project.root, parent, commit)
        if distance is not None:
            candidates.append((distance, other.id))
    if not candidates:
        return []
    nearest = min(distance for distance, _ in candidates)
    return sorted(experiment_id for distance, experiment_id in candidates if distance == nearest)


def enrich_experiments(
    project: Project,
    task_id: str,
    records: list[ExperimentRecord],
    runs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    runs = read_runs(project, task_id) if runs is None else runs
    task = read_task(project, task_id)
    try:
        task_commit: str | None = resolve_commit(project.root, task.branch)
    except HirakuError:
        task_commit = None
    result: list[dict[str, Any]] = []
    for record in records:
        experiment_runs = [row for row in runs if row.get("experiment_id") == record.id]
        bound = record.data["commit"]
        mismatches = [
            row["run_id"] for row in experiment_runs if bound and row.get("commit", "") != bound
        ]
        metrics = [
            row.get("metrics", {})
            if project.tracking_backend == "mlflow"
            else {key: value for key, value in row.items() if key not in PROVENANCE_COLUMNS}
            for row in experiment_runs
        ]
        divergence = (
            ahead_behind(project.root, task.branch, bound) if bound and task_commit else None
        )
        value = record.as_dict()
        value.update(
            {
                "run_count": len(experiment_runs),
                "runs": experiment_runs,
                "metrics": metrics,
                "git_parents": commit_parents(project.root, bound) if bound else [],
                "parent_experiments": _nearest_parent_experiments(project, record, records),
                "branch_exists": bool(
                    record.data["branch"] and branch_exists(project.root, record.data["branch"])
                ),
                "contained_in_task_branch": bool(
                    bound and task_commit and is_ancestor(project.root, bound, task.branch)
                ),
                "task_branch_ahead": divergence[0] if divergence else None,
                "experiment_ahead": divergence[1] if divergence else None,
                "commit_mismatches": mismatches,
            }
        )
        result.append(value)
    return result
