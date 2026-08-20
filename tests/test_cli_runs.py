from __future__ import annotations

import csv
import json
from pathlib import Path

from conftest import commit_all, run_process


def prepare(repo: Path, command: list[str]) -> None:
    run_process([*command, "init"], repo)
    commit_all(repo, "initialize")
    run_process([*command, "exp", "create", "ranking-demo"], repo)


def test_runs_json_and_direction_validation(repo: Path, hiraku_command: list[str]) -> None:
    prepare(repo, hiraku_command)
    table = repo / ".hiraku/runs.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    with table.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["experiment_id", "commit", "model", "accuracy"])
        writer.writeheader()
        writer.writerows(
            [
                {
                    "experiment_id": "ranking-demo",
                    "commit": "a",
                    "model": "low",
                    "accuracy": "0.6",
                },
                {
                    "experiment_id": "ranking-demo",
                    "commit": "b",
                    "model": "high",
                    "accuracy": "0.9",
                },
            ]
        )

    result = run_process(
        [
            *hiraku_command,
            "runs",
            "ranking-demo",
            "--sort",
            "accuracy",
            "--descending",
            "--json",
        ],
        repo,
    )
    rows = json.loads(result.stdout)
    assert [row["model"] for row in rows] == ["high", "low"]
    assert [row["_rank"] for row in rows] == [1, 2]

    invalid = run_process(
        [*hiraku_command, "runs", "ranking-demo", "--sort", "accuracy"],
        repo,
        check=False,
    )
    assert invalid.returncode == 1
    assert "exactly one" in invalid.stderr


def test_runs_missing_table_returns_empty_json(repo: Path, hiraku_command: list[str]) -> None:
    prepare(repo, hiraku_command)

    result = run_process([*hiraku_command, "runs", "ranking-demo", "--json"], repo)

    assert json.loads(result.stdout) == []
