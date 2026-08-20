from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.errors import HirakuError
from hiraku.run_table import read_runs


def write_table(path: Path, fieldnames: list[str], rows: list[list[str]]) -> None:
    delimiter = "\t" if path.suffix == ".tsv" else ","
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(fieldnames)
        writer.writerows(rows)


@pytest.mark.parametrize("suffix", [".csv", ".tsv"])
def test_filter_and_rank_numeric_rows_stably(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"runs{suffix}"
    write_table(
        path,
        ["experiment_id", "commit", "model", "score"],
        [
            ["other", "x", "ignored", "1"],
            ["demo", "a", "first-tie", "0.8"],
            ["demo", "b", "missing", ""],
            ["demo", "c", "best", "0.9"],
            ["demo", "d", "second-tie", "0.8"],
            ["demo", "e", "nan", "NaN"],
        ],
    )

    collection = read_runs(path, "demo", sort_by="score", descending=True)

    assert [row["model"] for row in collection.rows] == [
        "best",
        "first-tie",
        "second-tie",
        "missing",
        "nan",
    ]
    assert [row.get("_rank") for row in collection.rows] == [1, 2, 3, None, None]


def test_missing_table_is_empty(tmp_path: Path) -> None:
    assert read_runs(tmp_path / "runs.csv", "demo").rows == []


@pytest.mark.parametrize(
    ("fieldnames", "message"),
    [
        (["experiment_id", "score"], "missing required columns"),
        (["experiment_id", "commit", "_rank"], "reserved column"),
    ],
)
def test_malformed_or_conflicting_tables(
    tmp_path: Path, fieldnames: list[str], message: str
) -> None:
    path = tmp_path / "runs.csv"
    write_table(path, fieldnames, [["demo", "x", "1"][: len(fieldnames)]])

    with pytest.raises(HirakuError, match=message):
        read_runs(path, "demo", sort_by=fieldnames[-1], descending=True)
