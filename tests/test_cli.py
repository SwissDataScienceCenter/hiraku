from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_mcp_missing_sdk_dependency(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    monkeypatch.delitem(sys.modules, "hiraku.mcp", raising=False)
    result = invoke("mcp")
    assert result.exit_code == 1
    assert "MCP SDK dependency" in result.output
    assert invoke("--help").exit_code == 0


def test_workflow_cli_exposes_langgraph_controls_only() -> None:
    help_text = CliRunner().invoke(main, ["workflow", "--help"]).output
    root_help = CliRunner().invoke(main, ["--help"]).output

    assert "list" in help_text
    assert "graph" in help_text
    assert "start" in help_text
    assert "status" in help_text
    assert "resume" in help_text
    assert "send-event" not in help_text
    assert "session" not in root_help


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


def test_status_reports_project_tracking_backend(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    config.tracking_backend = {"type": "wandb", "project": "eurosat-rgb"}
    config.save()
    monkeypatch.chdir(tmp_path)
    result = invoke(
        "record",
        "create",
        "eurosat",
        "--type",
        "task",
        "--set",
        "metrics=[{name: val/accuracy, direction: maximize}]",
    )
    assert result.exit_code == 0
    child = invoke(
        "record", "create", "mlp", "--parent", "eurosat", "--type", "experiment"
    )
    assert child.exit_code == 0

    shown = json.loads(invoke("record", "show", "mlp", "--json").output)
    assert shown["values"]["metrics"] == [
        {"name": "val/accuracy", "direction": "maximize"}
    ]
    assert shown["sources"]["metrics"] == "mlp"
    assert "tracking_backend" not in shown["values"]

    raw = json.loads(invoke("record", "show", "mlp", "--raw", "--json").output)
    assert raw["values"]["metrics"] == [
        {"name": "val/accuracy", "direction": "maximize"}
    ]

    override = invoke(
        "record",
        "create",
        "cnn",
        "--parent",
        "eurosat",
        "--type",
        "experiment",
        "--set",
        "metrics=[{name: val/loss, direction: minimize}]",
    )
    assert override.exit_code == 0
    cnn = json.loads(invoke("record", "show", "cnn", "--raw", "--json").output)
    assert cnn["values"]["metrics"] == [{"name": "val/loss", "direction": "minimize"}]

    assert invoke("record", "set", "mlp").exit_code == 0
    status = json.loads(invoke("status", "--json").output)
    assert status["tracking_backend"] == "wandb"
    assert "tracking_backend" not in load_record("mlp", config=config).to_dict()


def test_record_list_displays_local_status(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "root", "--type", "task", "--set", "status=active")
    invoke("record", "create", "child", "--parent", "root")

    lines = invoke("record", "list").output.splitlines()
    assert "id" in lines[1]
    assert "parent" in lines[1]
    assert "type" in lines[1]
    assert "status" in lines[1]
    assert any("child" in line and "root" in line for line in lines)
    assert any("root" in line and "task" in line and "active" in line for line in lines)


def test_record_list_with_no_records(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "list")
    assert result.exit_code == 0
    assert "id" in result.output
    assert "parent" in result.output
    assert "status" in result.output


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


@pytest.mark.parametrize("value", ["null", "42"])
def test_record_update_rejects_invalid_markdown(
    value: str, tmp_path: Path, monkeypatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "test", "--type", "context").exit_code == 0
    result = invoke("record", "update", "test", "--set", f"markdown={value}")
    assert result.exit_code != 0
    assert "Use --markdown-file" in result.output
    assert invoke("record", "show", "test").exit_code == 0


def test_contexts_use_record_commands(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert (
        invoke(
            "record", "create", "literature", "--type", "context", "--set", "x=1"
        ).exit_code
        == 0
    )
    assert (
        json.loads(invoke("record", "show", "literature", "--raw", "--json").output)[
            "values"
        ]["x"]
        == 1
    )
    assert invoke("record", "update", "literature", "--set", "x=2").exit_code == 0
    for removed in ("context", "program", "action"):
        result = invoke(removed, "list")
        assert result.exit_code != 0
        assert "No such command" in result.output


@pytest.mark.parametrize(
    ("command", "header"),
    [
        ("record", ["id", "parent", "type", "status"]),
    ],
)
def test_generic_list_with_no_entries(
    command: str, header: list[str], tmp_path: Path, monkeypatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    result = invoke(command, "list")
    assert result.exit_code == 0
    assert all(column in result.output for column in header)


def test_edit_uses_stored_file(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "one")
    monkeypatch.setenv("EDITOR", "true")
    assert invoke("record", "edit", "one").exit_code == 0
    assert load_record("one").id == "one"
