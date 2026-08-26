from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any

from hiraku.config import Project
from hiraku.errors import ValidationError
from hiraku.git import commit_parents
from hiraku.records import ExperimentRecord, list_experiments, read_task, resolve_task
from hiraku.util import json_dump, render_table, validate_utc_timestamp

PROVENANCE_COLUMNS = ["run_id", "task_id", "experiment_id", "commit", "created_at"]


def add_runs_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("runs", help="Inspect tracked runs")
    command.add_argument("task_id", nargs="?")
    command.add_argument("--task")
    command.add_argument("--sort")
    directions = command.add_mutually_exclusive_group()
    directions.add_argument("--ascending", action="store_true")
    directions.add_argument("--descending", action="store_true")
    command.add_argument("--json", action="store_true")


def read_runs(project: Project, task_id: str) -> list[dict[str, str]]:
    read_task(project, task_id)
    path = project.runs_path(task_id)
    if not path.exists() or path.stat().st_size == 0:
        return []
    return read_table(path, task_id)


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


def numeric(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def sort_runs(rows: list[dict[str, str]], column: str, descending: bool) -> list[dict[str, Any]]:
    ranked: list[tuple[int, dict[str, str], float]] = []
    unranked: list[tuple[int, dict[str, str]]] = []
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


def show_runs(project: Project, args: argparse.Namespace) -> int:
    task_id = resolve_task(project, args.task_id, args.task)
    if args.sort and not (args.ascending or args.descending):
        raise ValidationError("--sort requires exactly one of --ascending or --descending.")
    if not args.sort and (args.ascending or args.descending):
        raise ValidationError("--ascending and --descending require --sort.")
    rows: list[dict[str, Any]] = annotate_commit_mismatches(
        read_runs(project, task_id), list_experiments(project, task_id)
    )
    if args.sort:
        if rows and args.sort not in rows[0]:
            raise ValidationError(
                f"Sort column {args.sort!r} does not exist in the tracking table."
            )
        rows = sort_runs(rows, args.sort, args.descending)
    if args.json:
        print(json_dump(rows))
    else:
        columns = list(rows[0]) if rows else None
        print(render_table(rows, columns))
    return 0


def annotate_commit_mismatches(
    rows: list[dict[str, str]], records: list[ExperimentRecord]
) -> list[dict[str, Any]]:
    bound = {record.id: record.data["commit"] for record in records}
    result: list[dict[str, Any]] = []
    for row in rows:
        expected = bound.get(row["experiment_id"], "")
        result.append(
            {
                **row,
                "_commit_mismatch": bool(expected and row.get("commit", "") != expected),
            }
        )
    return result


def enrich_experiments(
    project: Project, task_id: str, records: list[ExperimentRecord]
) -> list[dict[str, Any]]:
    runs = read_runs(project, task_id)
    metric_columns = [
        key for key in (list(runs[0]) if runs else []) if key not in PROVENANCE_COLUMNS
    ]
    result: list[dict[str, Any]] = []
    for record in records:
        experiment_runs = [row for row in runs if row["experiment_id"] == record.id]
        bound = record.data["commit"]
        mismatches = [
            row["run_id"] for row in experiment_runs if bound and row.get("commit", "") != bound
        ]
        value = record.as_dict()
        value.update(
            {
                "run_count": len(experiment_runs),
                "runs": experiment_runs,
                "metrics": [
                    {key: row.get(key, "") for key in metric_columns} for row in experiment_runs
                ],
                "git_parents": commit_parents(project.root, bound) if bound else [],
                "commit_mismatches": mismatches,
            }
        )
        result.append(value)
    return result
