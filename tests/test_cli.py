from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_record_cli_lifecycle(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "root", "--set", "goal=Improve").exit_code == 0
    assert invoke("record", "create", "child", "--parent", "root").exit_code == 0
    assert config.records_dir.joinpath("root/root.md").is_file()
    assert config.records_dir.joinpath("root/child.md").is_file()
    shown = invoke("record", "show", "child", "--json")
    assert shown.exit_code == 0
    payload = json.loads(shown.output)
    assert payload["values"]["goal"] == "Improve"
    assert payload["sources"]["goal"] == "root"
    raw = json.loads(invoke("record", "show", "child", "--raw", "--json").output)
    assert "goal" not in raw["values"]
    assert invoke("record", "update", "child", "--set", "goal=null").exit_code == 0
    assert (
        json.loads(invoke("record", "show", "child", "--json").output)["values"]["goal"]
        is None
    )
    assert invoke("record", "update", "child", "--unset", "goal").exit_code == 0
    assert (
        json.loads(invoke("record", "show", "child", "--json").output)["values"]["goal"]
        == "Improve"
    )
    assert invoke("record", "tree").output == "root\n  child\n"
    assert invoke("record", "delete", "root").exit_code != 0
    assert invoke("record", "delete", "child").exit_code == 0
    assert invoke("record", "delete", "root").exit_code == 0


def test_record_cli_active_and_validation(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "first").exit_code == 0
    assert HirakuConfig.load(config.path).active_record_id == "first"
    assert invoke("record", "create", "second", "--no-activate").exit_code == 0
    assert HirakuConfig.load(config.path).active_record_id == "first"
    assert invoke("record", "set", "second").exit_code == 0
    assert json.loads(invoke("status", "--json").output)["active_record_id"] == "second"
    assert invoke("record", "create", "third", "--parent", "missing").exit_code != 0
    assert invoke("record", "create", "first").exit_code != 0
    assert invoke("record", "update", "first", "--set", "id=other").exit_code != 0
    invalid_link = invoke("record", "update", "second", "--set", "related_to=[missing]")
    assert invalid_link.exit_code != 0
    assert invoke("record", "update", "second", "--related-to", "first").exit_code == 0


def test_record_list_displays_local_status(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "root", "--type", "task", "--set", "status=active")
    invoke("record", "create", "child", "--parent", "root")

    lines = invoke("record", "list").output.splitlines()
    assert lines[0].split() == ["id", "parent", "type", "status"]
    assert lines[2].split() == ["child", "root"]
    assert lines[3].split() == ["root", "task", "active"]


def test_record_cli_reparents_and_makes_root(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "one")
    invoke("record", "create", "two")
    invoke("record", "create", "child", "--parent", "one")
    assert invoke("record", "update", "child", "--parent", "two").exit_code == 0
    assert load_record("child").path == config.records_dir / "two/child.md"
    assert invoke("record", "update", "child", "--root").exit_code == 0
    assert load_record("child").path == config.records_dir / "child/child.md"


def test_record_cli_entrypoint_launch_and_runs(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert (
        invoke(
            "record",
            "create",
            "root",
            "--set",
            'entrypoints={train: {command: [python, -c, "print(7)"]}}',
        ).exit_code
        == 0
    )
    assert invoke("record", "create", "child", "--parent", "root").exit_code == 0
    entries = json.loads(invoke("record", "entrypoints", "child", "--json").output)
    assert entries["sources"] == {"train": "root"}
    result = invoke("launch", "train", "--record", "child", "--dry-run")
    assert result.exit_code == 0
    assert "python -c" in result.output
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.loss\na,root,0.5\nb,child,0.2\nc,,0.1\n"
    )
    direct = json.loads(invoke("runs", "--record", "root", "--json").output)
    all_runs = json.loads(invoke("runs", "--record", "root", "--json").output)
    assert {run["id"] for run in direct} == {"a", "b"}
    assert {run["id"] for run in all_runs} == {"a", "b"}
    sorted_table = invoke(
        "runs",
        "--record",
        "root",
        "--metrics",
        "loss",
        "--sort",
        "loss",
        "--ascending",
    )
    assert sorted_table.exit_code == 0
    assert [line.split()[0] for line in sorted_table.output.splitlines()[2:]] == [
        "b",
        "a",
    ]
    listed = invoke("runs", "list", "--record", "root", "--json")
    assert listed.exit_code == 0
    assert json.loads(listed.output) == direct


def test_generic_agent_commands_still_work(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("program", "create", "test", "--set", "purpose=review").exit_code == 0
    assert (
        json.loads(invoke("program", "show", "test", "--json").output)["purpose"]
        == "review"
    )
    assert (
        invoke("program", "update", "test", "--set", "purpose=inspect").exit_code == 0
    )
    assert invoke("context", "create", "literature").exit_code == 0
    assert invoke("action", "create", "record/review").exit_code == 0
    assert (
        json.loads(invoke("action", "show", "record/review", "--json").output)["id"]
        == "review"
    )
    assert invoke("action", "list", "--entity", "record").exit_code == 0
    assert (
        invoke("action", "update", "record/review", "--set", "status=ready").exit_code
        == 0
    )
    assert invoke("action", "delete", "record/review").exit_code == 0


def test_edit_uses_stored_file(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "one")
    monkeypatch.setenv("EDITOR", "true")
    assert invoke("record", "edit", "one").exit_code == 0
    assert load_record("one").id == "one"
