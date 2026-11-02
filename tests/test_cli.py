from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


@pytest.mark.parametrize(
    ("destination", "override", "identifier"),
    [
        ("notes/baseline.md", None, "baseline"),
        ("notes/baseline", None, "baseline"),
        ("notes/baseline", "override", "override"),
        ("notes/Display Name.md", "baseline", "baseline"),
        ("notes/ leading.md", None, None),
        ("notes/baseline.md", "bad/id", None),
        ("notes/baseline.txt", None, None),
        (".", None, None),
    ],
)
def test_create_infers_or_overrides_id(
    tmp_path, monkeypatch, destination, override, identifier
):
    config = HirakuConfig.init(tmp_path, records_path="knowledge", install_skill=False)
    monkeypatch.chdir(tmp_path)
    args = ["record", "create", destination]
    if override is not None:
        args.extend(["--id", override])
    result = invoke(*args)
    assert result.exit_code == (0 if identifier else 1)
    path = config.records_dir / destination
    if identifier:
        if not path.suffix:
            path = path.with_suffix(".md")
        assert Record.load(open_workspace(config), identifier).path == path
        assert path.is_file()
    else:
        assert not list(config.records_dir.rglob("*.md"))


def test_create_suffix_aliases_preserve_collision_checks(tmp_path, monkeypatch):
    config = HirakuConfig.init(tmp_path, records_path="knowledge", install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "baseline").exit_code == 0
    original = (config.records_dir / "baseline.md").read_bytes()
    assert invoke("record", "create", "baseline.md").exit_code == 1
    assert invoke("record", "create", "other/baseline").exit_code == 1
    assert (config.records_dir / "baseline.md").read_bytes() == original
    assert not (config.records_dir / "other/baseline.md").exists()


def test_fmql_query_cli_and_explicit_creation(tmp_path, monkeypatch):
    config = HirakuConfig.init(tmp_path / "code", records_path="../knowledge")
    monkeypatch.chdir(config.root)
    assert invoke("record", "create").exit_code == 2
    assert invoke("record", "create", "notes/example.md", "--id", "one").exit_code == 0
    result = invoke("record", "query", "MATCH (r) RETURN r.id")
    assert result.exit_code == 0
    assert json.loads(result.output) == {"columns": ["r.id"], "rows": [["one"]]}
    for query in ['MATCH (r) SET r.status = "done" RETURN r', "not a query"]:
        result = invoke("record", "query", query)
        assert result.exit_code == 1
        assert "Error:" in result.output
    assert Record.load(open_workspace(config), "one").status is None
    (config.records_dir / "calendar.md").write_text("---\ndue: 2026-11-02\n---\n")
    result = invoke("record", "query", "MATCH (p) WHERE p.due IS NOT NULL RETURN p.due")
    assert result.exit_code == 0
    assert json.loads(result.output)["rows"] == [["2026-11-02"]]


def test_init_with_external_records_path(tmp_path):
    project = tmp_path / "code"
    result = invoke("init", str(project), "--records-path", "../knowledge")
    assert result.exit_code == 0
    config = HirakuConfig.load(project / "hiraku.yml")
    assert config.records_dir == tmp_path / "knowledge"
    assert config.records_dir.is_dir()
    assert not (config.records_dir / ".git").exists()
    before = config.path.read_bytes()
    assert invoke("init", str(project), "--records-path", "../other").exit_code == 1
    assert config.path.read_bytes() == before


def test_mcp_missing_sdk_dependency(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
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
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    monkeypatch.chdir(tmp_path)
    assert (
        invoke("record", "create", "root/root.md", "--set", "goal=Improve").exit_code
        == 0
    )
    assert (
        invoke("record", "create", "child/child.md", "--parent", "root").exit_code == 0
    )
    assert config.records_dir.joinpath("root/root.md").is_file()
    assert config.records_dir.joinpath("child/child.md").is_file()
    shown = invoke("record", "show", "child", "--json")
    assert shown.exit_code == 0
    payload = json.loads(shown.output)
    assert "goal" not in payload
    assert invoke("record", "update", "child", "--set", "goal=null").exit_code == 0
    assert (
        json.loads(invoke("record", "show", "child", "--json").output)["goal"] is None
    )
    assert invoke("record", "update", "child", "--unset", "goal").exit_code == 0
    assert "goal" not in json.loads(invoke("record", "show", "child", "--json").output)
    assert invoke("record", "tree").output == "root\n  child\n"
    assert invoke("record", "delete", "root").exit_code != 0
    assert invoke("record", "delete", "child").exit_code == 0
    assert invoke("record", "delete", "root").exit_code == 0


def test_record_cli_active_and_validation(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "first/first.md").exit_code == 0
    assert HirakuConfig.load(config.path).active_record_id == "first"
    assert (
        invoke("record", "create", "second/second.md", "--no-activate").exit_code == 0
    )
    assert HirakuConfig.load(config.path).active_record_id == "first"
    assert invoke("record", "set", "second").exit_code == 0
    assert json.loads(invoke("status", "--json").output)["active_record_id"] == "second"
    assert (
        invoke("record", "create", "third/third.md", "--parent", "missing").exit_code
        != 0
    )
    assert invoke("record", "create", "first/first.md").exit_code != 0
    assert invoke("record", "update", "first", "--set", "id=other").exit_code != 0
    invalid_link = invoke("record", "update", "second", "--set", "related_to=[missing]")
    assert invalid_link.exit_code != 0
    assert invoke("record", "update", "second", "--related-to", "first").exit_code == 0


def test_status_reports_project_tracking_backend(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    config.tracking_backend = {"type": "wandb", "project": "eurosat-rgb"}
    config.save()
    monkeypatch.chdir(tmp_path)
    result = invoke(
        "record",
        "create",
        "eurosat/eurosat.md",
        "--type",
        "task",
        "--set",
        "metrics=[{name: val/accuracy, direction: maximize}]",
    )
    assert result.exit_code == 0
    child = invoke(
        "record",
        "create",
        "mlp/mlp.md",
        "--parent",
        "eurosat",
        "--type",
        "experiment",
    )
    assert child.exit_code == 0

    shown = json.loads(invoke("record", "show", "mlp", "--json").output)
    assert "metrics" not in shown
    assert "tracking_backend" not in shown

    override = invoke(
        "record",
        "create",
        "cnn/cnn.md",
        "--parent",
        "eurosat",
        "--type",
        "experiment",
        "--set",
        "metrics=[{name: val/loss, direction: minimize}]",
    )
    assert override.exit_code == 0
    cnn = json.loads(invoke("record", "show", "cnn", "--json").output)
    assert cnn["metrics"] == [{"name": "val/loss", "direction": "minimize"}]

    assert invoke("record", "set", "mlp").exit_code == 0
    status = json.loads(invoke("status", "--json").output)
    assert status["tracking_backend"] == "wandb"
    assert (
        "tracking_backend" not in Record.load(open_workspace(config), "mlp").to_dict()
    )


def test_record_list_displays_local_status(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    invoke(
        "record",
        "create",
        "root/root.md",
        "--type",
        "task",
        "--set",
        "status=active",
    )
    invoke("record", "create", "child/child.md", "--parent", "root")

    lines = invoke("record", "list").output.splitlines()
    assert "id" in lines[1]
    assert "parent" in lines[1]
    assert "type" in lines[1]
    assert "status" in lines[1]
    assert any("child" in line and "root" in line for line in lines)
    assert any("root" in line and "task" in line and "active" in line for line in lines)


def test_record_list_with_no_records(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "list")
    assert result.exit_code == 0
    assert "id" in result.output
    assert "parent" in result.output
    assert "status" in result.output


@pytest.mark.parametrize(
    "options",
    [
        ["--parent", "one", "--root"],
        ["--root", "--set", "parent_id=one"],
        ["--parent", "one", "--unset", "parent_id"],
    ],
)
def test_record_update_rejects_conflicting_parent_options(
    tmp_path, monkeypatch, options
):
    config = HirakuConfig.init(tmp_path, records_path="knowledge", install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert invoke("record", "create", "one").exit_code == 0
    assert invoke("record", "update", "one", *options).exit_code == 1
    assert Record.load(open_workspace(config), "one").parent_id is None


def test_record_cli_reparents_and_makes_root(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "one/one.md")
    invoke("record", "create", "two/two.md")
    invoke("record", "create", "child/child.md", "--parent", "one")
    assert invoke("record", "update", "child", "--parent", "two").exit_code == 0
    assert (
        Record.load(open_workspace(HirakuConfig.discover()), "child").path
        == config.records_dir / "child/child.md"
    )
    assert invoke("record", "update", "child", "--root").exit_code == 0
    assert (
        Record.load(open_workspace(HirakuConfig.discover()), "child").path
        == config.records_dir / "child/child.md"
    )


@pytest.mark.parametrize("value", ["null", "42"])
def test_record_update_rejects_invalid_markdown(
    value: str, tmp_path: Path, monkeypatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    assert (
        invoke("record", "create", "test/test.md", "--type", "context").exit_code == 0
    )
    result = invoke("record", "update", "test", "--set", f"markdown={value}")
    assert result.exit_code != 0
    assert "Cannot update fields" in result.output
    assert invoke("record", "show", "test").exit_code == 0


def test_contexts_use_record_commands(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    assert (
        invoke(
            "record",
            "create",
            "literature/literature.md",
            "--type",
            "context",
            "--set",
            "x=1",
        ).exit_code
        == 0
    )
    assert json.loads(invoke("record", "show", "literature", "--json").output)["x"] == 1
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
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    result = invoke(command, "list")
    assert result.exit_code == 0
    assert all(column in result.output for column in header)


def test_edit_uses_stored_file(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    invoke("record", "create", "one/one.md")
    monkeypatch.setenv("EDITOR", "true")
    assert invoke("record", "edit", "one").exit_code == 0
    assert Record.load(open_workspace(HirakuConfig.discover()), "one").id == "one"
