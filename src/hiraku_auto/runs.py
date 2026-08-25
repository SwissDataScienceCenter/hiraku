"""The run table: append-only CSV or TSV, one row per launch.

`experiment_id` and `commit` are required. Everything else — provenance columns and the project's
own metrics — is carried verbatim, so a project can record whatever it measures without the tool
knowing about it in advance.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import HirakuError

REQUIRED_COLUMNS = ("experiment_id", "commit")

# Written by the launcher. A project never has to produce these itself, which is what lets the
# promotion gate rely on them.
PROVENANCE_COLUMNS = (
    "run_id",
    "experiment_id",
    "commit",
    "run_kind",
    "reproducible",
    "launched_at",
    "entrypoint",
    "exit_code",
    "duration_s",
    "seed",
    "python_version",
    "lockfile_sha256",
    "interpreter",
    "venv",
    "platform",
    "accelerator",
)


@dataclass
class RunTable:
    path: Path
    fieldnames: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)

    def for_experiment(self, experiment_id: str) -> list[dict[str, str]]:
        return [r for r in self.rows if r.get("experiment_id") == experiment_id]


def _delimiter(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return ","
    if suffix == ".tsv":
        return "\t"
    raise HirakuError(f"Run table must end in .csv or .tsv, got {path.name}")


def read_runs(path: Path) -> RunTable:
    if not path.is_file():
        return RunTable(path=path)
    delimiter = _delimiter(path)
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=delimiter)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                raise HirakuError(f"Run table {path} has no header row.")
            if len(fieldnames) != len(set(fieldnames)):
                raise HirakuError(f"Run table {path} has duplicate column names.")
            missing = set(REQUIRED_COLUMNS) - set(fieldnames)
            if missing:
                raise HirakuError(
                    f"Run table {path} is missing required columns: {', '.join(sorted(missing))}"
                )
            rows = [
                {k: (v if v is not None else "") for k, v in row.items() if k} for row in reader
            ]
    except csv.Error as exc:
        raise HirakuError(f"Malformed data in {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise HirakuError(f"Run table {path} is not valid UTF-8.") from exc
    return RunTable(path=path, fieldnames=fieldnames, rows=rows)


def append_run(path: Path, row: dict[str, Any]) -> None:
    """Append one row, widening the header if the row carries new columns.

    Rewriting to widen is not an edit of recorded data: existing values are preserved exactly and
    only empty cells are added, so the table stays append-only in meaning.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    delimiter = _delimiter(path)
    stringified = {k: ("" if v is None else str(v)) for k, v in row.items()}

    existing = read_runs(path) if path.is_file() else RunTable(path=path)
    fieldnames = list(existing.fieldnames)
    for key in stringified:
        if key not in fieldnames:
            fieldnames.append(key)

    if not existing.fieldnames:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            writer.writerow({k: stringified.get(k, "") for k in fieldnames})
        return

    if fieldnames != existing.fieldnames:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            for old in existing.rows:
                writer.writerow({k: old.get(k, "") for k in fieldnames})
            writer.writerow({k: stringified.get(k, "") for k in fieldnames})
        return

    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=delimiter)
        writer.writerow({k: stringified.get(k, "") for k in fieldnames})


def rank(
    rows: list[dict[str, str]], sort_by: str, descending: bool
) -> list[dict[str, Any]]:
    """Rank rows by a numeric column. Non-numeric and missing values keep their order, last."""
    ranked: list[tuple[float, dict[str, str]]] = []
    unranked: list[dict[str, str]] = []
    for row in rows:
        try:
            value = float(row.get(sort_by, ""))
        except (TypeError, ValueError):
            unranked.append(row)
            continue
        if not math.isfinite(value):
            unranked.append(row)
            continue
        ranked.append((value, row))
    ranked.sort(key=lambda item: item[0], reverse=descending)
    out: list[dict[str, Any]] = [{**row, "_rank": i} for i, (_, row) in enumerate(ranked, 1)]
    out.extend(unranked)
    return out


def best(
    rows: list[dict[str, str]], metric: str, direction: str
) -> dict[str, str] | None:
    ordered = rank(rows, metric, descending=(direction == "maximize"))
    for row in ordered:
        if "_rank" in row:
            return {k: v for k, v in row.items() if k != "_rank"}
    return None
