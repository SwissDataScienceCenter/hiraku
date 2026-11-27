from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project


def invoke(*args: str):
    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_version_outside_project(tmp_path, monkeypatch) -> None:
    from email.message import Message
    from types import SimpleNamespace

    from hiraku import cli

    info = Message()
    info["Provides-Extra"] = "wandb"
    info["Provides-Extra"] = "dashboard"
    distribution = SimpleNamespace(version="1.2.3", metadata=info, requires=[])
    monkeypatch.setattr(cli.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(sys, "argv", ["/env/bin/hiraku"])
    monkeypatch.setattr(sys, "executable", "/env/bin/python")
    monkeypatch.chdir(tmp_path)

    result = invoke("--version")

    assert result.exit_code == 0
    assert result.output == (
        "hiraku 1.2.3\n"
        "Executable: /env/bin/hiraku\n"
        f"Python: /env/bin/python ({sys.version.split()[0]})\n"
        "Extras:\n"
        "  dashboard: available\n"
        "  wandb: available\n"
    )


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ({"example": "2.0"}, "available"),
        ({}, "not installed"),
        ({"example": "1.0"}, "incompatible"),
    ],
)
def test_extra_dependency_status(monkeypatch, installed, expected) -> None:
    from hiraku import cli

    def version(name):
        if name not in installed:
            raise cli.metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(cli.metadata, "version", version)
    requirements = [
        'example>=2; extra == "dashboard"',
        'other; extra == "wandb"',
        'platform-only; extra == "dashboard" and python_version < "3.0"',
        "base>=1",
        'conditional-base; python_version >= "3.0"',
    ]
    assert cli._extra_status("dashboard", requirements) == expected


def test_root_help_lists_primary_commands() -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    for command in ("init", "record", "run", "job", "leaderboard", "workflow"):
        assert command in result.output


def test_runs_command_is_under_leaderboard() -> None:
    assert invoke("runs", "--help").exit_code != 0
    assert invoke("leaderboard", "runs", "--help").exit_code == 0


def test_record_tree_includes_missing_parents_and_cycles(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    for identifier, parent in (
        ("child", "root"),
        ("root", None),
        ("orphan", "missing"),
        ("a", "b"),
        ("b", "a"),
        ("self", "self"),
    ):
        project.vault.create(identifier, type="task", parent_id=parent)
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "tree")

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "orphan",
        "root",
        "  child",
        "a",
        "  b",
        "self",
    ]


def test_record_list_shows_research_state(tmp_path: Path, monkeypatch) -> None:
    project = Project.create(tmp_path, install_skill=False)
    project.vault.create(
        "experiment", type="experiment", status="draft", state="implemented"
    )
    monkeypatch.chdir(tmp_path)
    result = invoke("record", "list")
    assert result.exit_code == 0
    assert "state" in result.output
    assert "implemented" in result.output
    data = json.loads(invoke("record", "list", "--json").output)
    assert data[0]["status"] == "draft"
    assert data[0]["state"] == "implemented"


def test_record_create_persists_parent_without_changing_body(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("## Hypothesis\n\nTest it.\n", encoding="utf-8")
    assert (
        invoke(
            "record",
            "create",
            "Parent-A/Parent-A",
            "--title",
            "Parent [A]",
            "--type",
            "task",
        ).exit_code
        == 0
    )

    result = invoke(
        "record",
        "create",
        "Parent-A/child/child",
        "--title",
        "child",
        "--type",
        "task",
        "--parent",
        "Parent-A/Parent-A",
        "--markdown-file",
        str(body),
    )

    assert result.exit_code == 0, result.output
    child = project.vault.load("Parent-A/child/child")
    assert child.markdown == "## Hypothesis\n\nTest it."
    assert child.parent_id == "Parent-A/Parent-A"
    assert (
        invoke(
            "record",
            "create",
            "Parent-A/child/empty/empty",
            "--title",
            "empty",
            "--type",
            "task",
            "--parent",
            "Parent-A/child/child",
        ).exit_code
        == 0
    )
    empty = project.vault.load("Parent-A/child/empty/empty")
    assert empty.markdown == ""
    assert empty.parent_id == child.id


def test_record_create_rejects_record_ids_with_spaces(
    tmp_path: Path, monkeypatch
) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "create", "experiments/my run", "--type", "task")

    assert result.exit_code == 1
    assert "Invalid record ID" in result.output


def test_unknown_command_uses_click_error() -> None:
    result = invoke("not-a-command")

    assert result.exit_code == 2
    assert "No such command" in result.output
    assert "Traceback" not in result.output


def test_domain_error_is_reported_as_click_error(tmp_path: Path, monkeypatch) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "show", "missing")

    assert result.exit_code == 1
    assert "Record 'missing' not found" in result.output
    assert "Traceback" not in result.output


def test_workflow_help_lists_supported_commands() -> None:
    result = invoke("workflow", "--help")

    assert result.exit_code == 0
    for command in ("list", "graph", "start", "status", "resume"):
        assert command in result.output
    assert "send-event" not in result.output


@pytest.mark.parametrize("args", [("mcp", "serve")])
def test_mcp_missing_sdk_dependency(tmp_path: Path, monkeypatch, args) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    monkeypatch.delitem(sys.modules, "hiraku.mcp", raising=False)

    result = invoke(*args)

    assert result.exit_code == 1
    assert "MCP SDK dependency" in result.output


def test_mcp_requires_explicit_subcommand() -> None:
    result = invoke("mcp")

    assert result.exit_code == 2
    assert all(command in result.output for command in ("serve", "install"))


def test_mcp_install_preserves_other_client_settings(
    tmp_path: Path, monkeypatch
) -> None:
    Project.create(tmp_path, install_skill=False, install_mcp=False)
    (tmp_path / "uv.lock").touch()
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}})
    )
    (tmp_path / ".pi").mkdir()
    (tmp_path / ".pi/mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}})
    )
    (tmp_path / ".codex").mkdir(exist_ok=True)
    codex = tmp_path / ".codex/config.toml"
    codex.write_text('model = "local"\n\n[mcp_servers.other]\ncommand = "other"\n')
    monkeypatch.chdir(tmp_path)

    installed = invoke("mcp", "install")

    assert installed.exit_code == 0, installed.output
    server = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["hiraku"]
    assert server == {"command": "uv", "args": ["run", "hiraku", "mcp", "serve"]}
    assert '"hiraku"' in (tmp_path / ".pi/mcp.json").read_text()
    assert 'args = ["run", "hiraku", "mcp", "serve"]' in codex.read_text()

    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"] == {
        "other": {"command": "other"},
        "hiraku": server,
    }
    assert json.loads((tmp_path / ".pi/mcp.json").read_text())["mcpServers"] == {
        "other": {"command": "other"},
        "hiraku": server,
    }
    assert 'model = "local"' in codex.read_text()
    assert "[mcp_servers.other]" in codex.read_text()
    assert "[mcp_servers.hiraku]" in codex.read_text()


def test_example_list_works_outside_a_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = invoke("example", "list")

    assert result.exit_code == 0
    assert "digits" in result.output
    assert "Digits" in result.output
    assert "Compare regularization strengths" in result.output
    assert list(tmp_path.iterdir()) == []


def test_init_and_example_create_accept_destination(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    plain = invoke("init", "research")
    example = invoke("example", "create", "digits", "demo")

    assert plain.exit_code == 0, plain.output
    assert example.exit_code == 0, example.output
    assert Project.load(tmp_path / "research/hiraku.yml").root == (
        tmp_path / "research"
    )
    assert (tmp_path / "research/.codex/agents/literature-reviewer.toml").is_file()
    assert (tmp_path / "research/.claude/agents/literature-reviewer.md").is_file()
    assert Project.load(tmp_path / "demo/hiraku.yml").root == tmp_path / "demo"
    assert (tmp_path / "demo/train.py").is_file()
    assert (tmp_path / "demo/README.md").is_file()
    assert (tmp_path / "demo/hiraku/leaderboards/default.yml").is_file()
    for path in (
        ".agents/skills/hiraku/SKILL.md",
        ".claude/skills/hiraku/SKILL.md",
        ".mcp.json",
        ".pi/mcp.json",
        ".codex/config.toml",
    ):
        assert (tmp_path / "demo" / path).read_bytes() == (
            tmp_path / "research" / path
        ).read_bytes()
    assert not (tmp_path / "demo/hiraku/workflows").exists()
    assert not list((tmp_path / "demo/hiraku/records").rglob("*.md"))
    assert not (tmp_path / "demo/mlflow.db").exists()


def test_init_rejects_template_option(tmp_path: Path) -> None:
    target = tmp_path / "project"
    result = invoke("init", str(target), "--template", str(tmp_path))

    assert result.exit_code == 2
    assert "No such option" in result.output
    assert "--template" in result.output
    assert not target.exists()


def test_example_create_copies_prepared_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = invoke("example", "create", "digits", "--with-results")

    assert result.exit_code == 0, result.output
    project = Project.load(tmp_path / "digits/hiraku.yml")
    assert (project.root / "train.py").is_file()
    assert (project.root / "mlflow.db").is_file()
    assert project.vault.load("digit-classification/weak-l2").title == (
        "Weak L2 regularization"
    )


def test_init_reports_existing_project_without_overwriting_it(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project.create(tmp_path)
    marker_before = project.path.read_bytes()
    monkeypatch.chdir(tmp_path)

    result = invoke("init")

    assert result.exit_code == 0
    assert "already initialized" in result.output
    assert project.path.read_bytes() == marker_before


def test_example_create_requires_example(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    missing_example = invoke("example", "create")

    assert missing_example.exit_code == 2
    assert list(tmp_path.iterdir()) == []


def test_runs_report_duration(tmp_path: Path, monkeypatch) -> None:
    create_tracked_project(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    assert (
        invoke(
            "record", "create", "root/root", "--title", "root", "--type", "task"
        ).exit_code
        == 0
    )
    (tmp_path / "runs.csv").write_text(
        "id,record_id,duration\na,root/root,4.25\nb,root/root,\n"
    )

    rows = json.loads(
        invoke("leaderboard", "runs", "--record", "root/root", "--json").output
    )
    table = invoke("leaderboard", "runs", "--record", "root/root").output

    assert [row["duration"] for row in rows] == [4.25, None]
    assert all(
        column in table
        for column in ("id", "record", "timestamp", "duration", "commit")
    )
    assert "a" in table
    assert "root" in table
    assert "4.25" in table


def test_runs_json_keeps_extra_fields_in_raw(tmp_path: Path, monkeypatch) -> None:
    project = create_tracked_project(tmp_path, install_skill=False)
    project.vault.create("root/root", type="task", title="root")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs.csv").write_text("id,record_id,dataset\na,root/root,iris\n")

    result = invoke("leaderboard", "runs", "--record", "root/root", "--json")

    assert result.exit_code == 0
    run = json.loads(result.output)[0]
    assert "dataset" not in run
    assert run["raw"]["dataset"] == "iris"


def test_explicit_run_and_run_queries(tmp_path: Path, monkeypatch) -> None:
    create_tracked_project(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    assert (
        invoke(
            "record", "create", "root/root", "--title", "root", "--type", "task"
        ).exit_code
        == 0
    )
    assert (
        invoke(
            "record",
            "create",
            "root/child/child",
            "--title",
            "child",
            "--type",
            "task",
            "--parent",
            "root/root",
        ).exit_code
        == 0
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "records"], cwd=tmp_path, check=True)
    result = invoke(
        "run",
        "--record",
        "root/child/child",
        "--",
        sys.executable,
        "-c",
        "from pathlib import Path; Path('ran.txt').write_text('7')",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ran.txt").read_text() == "7"
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.loss\na,root/root,0.5\nb,root/child/child,0.2\nc,,0.1\n"
    )
    direct = json.loads(
        invoke("leaderboard", "runs", "--record", "root/root", "--json").output
    )
    attributed_runs = json.loads(invoke("leaderboard", "runs", "--json").output)
    assert {run["id"] for run in direct} == {"a"}
    assert {run["id"] for run in attributed_runs} == {"a", "b"}
    sorted_table = invoke(
        "leaderboard",
        "runs",
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
        if (cells := line.split()) and cells[0] in {"a", "b"}
    ]
    assert [row[0] for row in data_rows] == ["b", "a"]
    assert invoke("leaderboard", "runs", "list", "--record", "root/root").exit_code != 0


def test_runs_default_scope_is_all_attributed_runs(tmp_path: Path, monkeypatch) -> None:
    create_tracked_project(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,record_id\na,root/root\nb,root/child/child\nc,\n"
    )
    no_active = invoke("leaderboard", "runs", "--json")
    assert no_active.exit_code == 0
    assert {run["id"] for run in json.loads(no_active.output)} == {"a", "b"}
    assert (
        invoke(
            "record",
            "create",
            "root/root",
            "--title",
            "root",
            "--type",
            "task",
        ).exit_code
        == 0
    )
    assert (
        invoke(
            "record",
            "create",
            "root/child/child",
            "--title",
            "child",
            "--type",
            "task",
            "--parent",
            "root/root",
        ).exit_code
        == 0
    )

    def ids(*args: str) -> set[str]:
        result = invoke("leaderboard", "runs", *args, "--json")
        assert result.exit_code == 0
        return {run["id"] for run in json.loads(result.output)}

    assert ids() == {"a", "b"}
    assert ids("--include-unattributed") == {"a", "b", "c"}
    assert ids("--record", "root/root", "--include-unattributed") == {"a"}
    assert ids("--record", "root/root") == {"a"}
    assert ids("--record", "missing") == set()


def test_record_update_allows_unfinished_graph_relationships(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    parent = project.vault.create("parent/parent", type="task", title="parent")
    child = project.vault.create(
        f"{parent.id.rpartition('/')[0]}/child/child",
        type="task",
        parent_id=parent.id,
        title="child",
    )
    monkeypatch.chdir(tmp_path)

    cycle = invoke("record", "update", parent.id, "--set", f"parent_id={child.id}")
    removed_option = invoke("record", "update", child.id, "--root")
    missing = invoke("record", "update", child.id, "--related-to", "missing")

    assert cycle.exit_code == 0
    assert removed_option.exit_code != 0
    assert missing.exit_code == 0
    assert project.vault.load(parent.id).parent_id == child.id


def test_record_update_preserves_child_parent(tmp_path: Path, monkeypatch) -> None:
    project = Project.create(tmp_path, install_skill=False)
    parent = project.vault.create("parent/parent", type="task", title="parent")
    child = project.vault.create(
        f"{parent.id.rpartition('/')[0]}/child/child",
        type="record",
        parent_id=parent.id,
        title="child",
    )
    monkeypatch.chdir(tmp_path)

    result = invoke("record", "update", child.id, "--set", "count=1")

    assert result.exit_code == 0, result.output
    updated = project.vault.load(child.id)
    assert updated.parent_id == parent.id
    assert updated.count == 1
    assert project.vault.load(child.id).related_to == []


def create_tracked_project(*args, **kwargs):
    project = Project.create(*args, **kwargs)
    (project.leaderboards_dir / "default.yml").write_text(
        "source: {type: csv, path: runs.csv}\n"
    )
    return project


def test_runs_require_view_selection_and_discover_parameters(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    monkeypatch.chdir(tmp_path)
    missing = invoke("leaderboard", "runs")
    assert missing.exit_code != 0
    assert "No leaderboards" in missing.output
    for name in ("a", "b"):
        (project.leaderboards_dir / f"{name}.yml").write_text(
            f"source: {{type: csv, path: {name}.csv}}\n"
        )
    ambiguous = invoke("leaderboard", "runs")
    assert ambiguous.exit_code != 0
    assert "Select a leaderboard by name. Available: a, b" in ambiguous.output
    (tmp_path / "b.csv").write_text(
        "id,record_id,metric.score,param.seed\nr,unknown,0.9,42\n"
    )
    result = invoke("leaderboard", "runs", "b")
    assert result.exit_code == 0, result.output
    assert "score" in result.output
    assert "seed" in result.output
    assert "42" in result.output
    unknown = invoke("leaderboard", "runs", "missing")
    assert unknown.exit_code != 0
    assert "Cannot read YAML" in unknown.output


@pytest.fixture
def project(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    monkeypatch.chdir(tmp_path)
    return project


def test_leaderboard_lifecycle(project):
    result = invoke(
        "leaderboard", "create", "local", "--source", "csv", "--path", "runs.csv"
    )
    assert result.exit_code == 0, result.output
    path = project.leaderboards_dir / "local.yml"
    assert path.is_file()
    assert json.loads(invoke("leaderboard", "list", "--json").output) == ["local"]
    shown = invoke("leaderboard", "show", "local", "--json")
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == {"source": {"type": "csv", "path": "runs.csv"}}
    source = project.root / "runs.csv"
    source.write_text("id,metric.score\nr,1\n")
    runs = invoke("leaderboard", "runs", "local", "--include-unattributed", "--json")
    assert runs.exit_code == 0, runs.output
    assert json.loads(runs.output)[0]["metrics"] == {"score": 1}
    before = path.read_bytes()
    duplicate = invoke(
        "leaderboard", "create", "local", "--source", "csv", "--path", "runs.csv"
    )
    assert duplicate.exit_code != 0
    assert "already exists" in duplicate.output
    assert path.read_bytes() == before
    deleted = invoke("leaderboard", "delete", "local")
    assert deleted.exit_code == 0, deleted.output
    assert not path.exists()
    assert source.read_text() == "id,metric.score\nr,1\n"


@pytest.mark.parametrize(
    "options, expected",
    [
        (
            ["--source", "csv", "--path", "results.csv"],
            {"type": "csv", "path": "results.csv"},
        ),
        (
            [
                "--source",
                "mlflow",
                "--tracking-uri",
                "http://localhost:5000",
                "--experiment-name",
                "test",
            ],
            {
                "type": "mlflow",
                "tracking_uri": "http://localhost:5000",
                "experiment_name": "test",
            },
        ),
        (
            ["--source", "wandb", "--entity", "team", "--project", "training"],
            {
                "type": "wandb",
                "entity": "team",
                "project": "training",
                "mode": "online",
            },
        ),
    ],
)
def test_leaderboard_source_options(project, options, expected, monkeypatch):
    monkeypatch.setenv("WANDB_API_KEY", "test-secret")
    result = invoke("leaderboard", "create", "test", *options)
    assert result.exit_code == 0, result.output
    shown = invoke("leaderboard", "show", "test", "--json")
    assert json.loads(shown.output)["source"] == expected
    assert "test-secret" not in shown.output
    assert "test-secret" not in (project.leaderboards_dir / "test.yml").read_text()


@pytest.mark.parametrize(
    "arguments",
    [
        ["missing-path", "--source", "csv"],
        ["blank-path", "--source", "csv", "--path", ""],
        ["../escape", "--source", "csv"],
        ["bad", "--source", "csv", "--tracking-uri", "https://example.com"],
        ["bad", "--source", "wandb"],
        [
            "bad",
            "--source",
            "mlflow",
            "--tracking-uri",
            "sqlite:///runs.db",
        ],
    ],
)
def test_invalid_leaderboard_creation_does_not_write_files(project, arguments):
    result = invoke("leaderboard", "create", *arguments)
    assert result.exit_code != 0
    assert list(project.leaderboards_dir.iterdir()) == []


def test_list_and_delete_invalid_leaderboard(project):
    path = project.leaderboards_dir / "broken.yml"
    path.write_text("source: [invalid]\n")
    result = invoke("leaderboard", "list", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == ["broken"]
    shown = invoke("leaderboard", "show", "broken")
    assert shown.exit_code != 0
    assert "Invalid leaderboard" in shown.output
    assert invoke("leaderboard", "delete", "broken").exit_code == 0
    assert not path.exists()
    assert invoke("leaderboard", "delete", "missing").exit_code != 0


def test_edit_leaderboard_validates_changes(project, monkeypatch):
    path = project.leaderboards_dir / "local.yml"
    path.write_text("source: [invalid]\n")

    def edit_file(filename, **kwargs):
        assert filename == path
        path.write_text(
            "source: {type: csv, path: runs.csv}\n"
            "metrics: [{name: score, direction: maximize}]\n"
        )

    monkeypatch.setattr("hiraku.cli._edit", edit_file)
    result = invoke("leaderboard", "edit", "local")
    assert result.exit_code == 0, result.output
    assert "Validated leaderboard" in result.output

    def invalid_edit(filename, **kwargs):
        path.write_text("metrics: invalid\n")

    monkeypatch.setattr("hiraku.cli._edit", invalid_edit)
    result = invoke("leaderboard", "edit", "local")
    assert result.exit_code != 0
    assert "Invalid leaderboard" in result.output
