from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from hiraku.errors import HirakuError

REQUIRED_COLUMNS = {"experiment_id", "commit"}


@dataclass(frozen=True)
class RunCollection:
    fieldnames: list[str]
    rows: list[dict[str, str | int]]


def _delimiter(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        return ","
    if path.suffix.lower() == ".tsv":
        return "\t"
    raise HirakuError("Run table path must end in .csv or .tsv.")


def read_runs(
    path: Path,
    experiment_id: str,
    *,
    sort_by: str | None = None,
    descending: bool = False,
) -> RunCollection:
    if not path.exists():
        return RunCollection(fieldnames=[], rows=[])
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=_delimiter(path))
            fieldnames = reader.fieldnames
            if fieldnames is None:
                raise HirakuError(f"Run table {path} has no header.")
            if len(fieldnames) != len(set(fieldnames)):
                raise HirakuError(f"Run table {path} contains duplicate column names.")
            missing = REQUIRED_COLUMNS - set(fieldnames)
            if missing:
                raise HirakuError(
                    f"Run table {path} is missing required columns: {', '.join(sorted(missing))}"
                )
            rows: list[dict[str, str | int]] = []
            for line_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise HirakuError(f"Malformed row in {path} at line {line_number}.")
                if row["experiment_id"] == experiment_id:
                    rows.append(dict(row))
    except csv.Error as exc:
        raise HirakuError(f"Malformed CSV/TSV data in {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise HirakuError(f"Run table {path} is not valid UTF-8.") from exc
    except OSError as exc:
        raise HirakuError(f"Could not read run table {path}: {exc}") from exc

    if sort_by is None:
        return RunCollection(fieldnames=fieldnames, rows=rows)
    if sort_by not in fieldnames:
        raise HirakuError(f"Sort column '{sort_by}' does not exist in the run table.")
    if "_rank" in fieldnames:
        raise HirakuError("Cannot rank a run table that already contains reserved column '_rank'.")

    ranked: list[tuple[float, dict[str, str | int]]] = []
    unranked: list[dict[str, str | int]] = []
    for row in rows:
        try:
            number = float(row[sort_by])
        except TypeError, ValueError:
            unranked.append(row)
            continue
        if not math.isfinite(number):
            unranked.append(row)
            continue
        ranked.append((number, row))
    ranked.sort(key=lambda item: item[0], reverse=descending)
    output: list[dict[str, str | int]] = []
    for rank, (_, row) in enumerate(ranked, 1):
        output.append({**row, "_rank": rank})
    output.extend(unranked)
    return RunCollection(fieldnames=[*fieldnames, "_rank"], rows=output)
