from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_runs_report_duration(tmp_path: Path, monkeypatch) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "root").exit_code == 0
    (tmp_path / "runs.csv").write_text("id,record_id,duration\na,root,4.25\nb,root,\n")

    rows = json.loads(invoke("runs", "--record", "root", "--json").output)
    table = invoke("runs", "--record", "root").output

    assert [row["duration"] for row in rows] == [4.25, None]
    assert all(
        column in table
        for column in ("id", "record", "timestamp", "duration", "commit")
    )
    assert "a" in table
    assert "root" in table
    assert "4.25" in table


def test_record_cli_explicit_run_and_runs(tmp_path: Path, monkeypatch) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    assert invoke("record", "create", "root").exit_code == 0
    assert invoke("record", "create", "child", "--parent", "root").exit_code == 0
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "records"], cwd=tmp_path, check=True)
    result = invoke(
        "run",
        "--record",
        "child",
        "--",
        sys.executable,
        "-c",
        "from pathlib import Path; Path('ran.txt').write_text('7')",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ran.txt").read_text() == "7"
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.loss\na,root,0.5\nb,child,0.2\nc,,0.1\n"
    )
    direct = json.loads(invoke("runs", "--record", "root", "--json").output)
    all_runs = json.loads(invoke("runs", "--record", "root", "--all", "--json").output)
    assert {run["id"] for run in direct} == {"a"}
    assert {run["id"] for run in all_runs} == {"a", "b"}
    sorted_table = invoke(
        "runs",
        "--record",
        "root",
        "--all",
        "--metrics",
        "loss",
        "--sort",
        "loss",
        "--ascending",
    )
    assert sorted_table.exit_code == 0
    data_rows = [
        cells
        for line in sorted_table.output.splitlines()
        if line.startswith("│")
        if (cells := [cell.strip() for cell in line.split("│")[1:-1]])
        and cells[0] != "id"
    ]
    assert [row[0] for row in data_rows] == ["b", "a"]
    assert invoke("runs", "list", "--record", "root").exit_code != 0


def test_runs_default_scope_is_active_record(tmp_path: Path, monkeypatch) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs.csv").write_text("id,record_id\na,root\nb,child\nc,\n")
    no_active = invoke("runs", "--json")
    assert no_active.exit_code != 0
    assert "No active record" in no_active.output
    assert invoke("record", "create", "root", "--no-activate").exit_code == 0
    assert invoke("record", "create", "child", "--parent", "root").exit_code == 0

    def ids(*args: str) -> set[str]:
        result = invoke("runs", *args, "--json")
        assert result.exit_code == 0
        return {run["id"] for run in json.loads(result.output)}

    assert ids() == {"b"}
    assert ids("--all") == {"a", "b"}
    assert ids("--record", "root") == {"a"}
    assert ids("--record", "root", "--all") == {"a", "b"}
