from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_dashboard_launch(tmp_path, monkeypatch) -> None:
    from hiraku import cli

    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_spec", lambda name: object())
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 3)
    result = invoke("dashboard", "--port", "2721")
    assert result.exit_code == 3
    command = calls[0]
    assert command[:4] == [cli.sys.executable, "-m", "marimo", "run"]
    notebook = Path(command[4])
    assert notebook.is_file()
    assert notebook.parent.joinpath("views/dashboard/index.html").is_file()
    assert command[5:] == ["--no-sandbox", "--port", "2721"]
    assert invoke("dashboard").exit_code == 3
    assert calls[-1][5:] == ["--no-sandbox"]
    assert invoke("dashboard", "--port", "0").exit_code == 2


def test_dashboard_missing_dependencies(tmp_path, monkeypatch) -> None:
    from hiraku import cli

    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_spec", lambda name: None)
    result = invoke("dashboard")
    assert result.exit_code == 1
    assert "uv sync --extra dashboard" in result.output


def test_project_owned_dashboard(tmp_path, monkeypatch) -> None:
    from hiraku import cli

    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_spec", lambda name: None)
    assert invoke("dashboard", "--init").exit_code == 0
    local = config.hiraku_dir / "dashboard"
    notebook = local / "app.py"
    notebook.write_text("# Custom dashboard\n")
    assert invoke("dashboard", "--init").exit_code == 1
    assert notebook.read_text() == "# Custom dashboard\n"
    assert local.joinpath("views/dashboard/index.html").is_file()
    monkeypatch.setattr(cli, "find_spec", lambda name: object())
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda args: calls.append(args) or 0)
    assert invoke("dashboard").exit_code == 0
    assert calls[0][4] == str(notebook)
    notebook.unlink()
    assert "not found" in invoke("dashboard").output


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
    assert [line.split()[0] for line in sorted_table.output.splitlines()[2:]] == [
        "b",
        "a",
    ]
    assert invoke("runs", "list", "--record", "root").exit_code != 0


def test_runs_default_scope_is_active_record(tmp_path: Path, monkeypatch) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
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
