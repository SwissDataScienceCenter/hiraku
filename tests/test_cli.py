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


def test_root_help_lists_primary_commands() -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    for command in ("init", "record", "run", "runs", "workflow"):
        assert command in result.output


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
            "Parent -A/Parent -A",
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
        "Parent -A/child/child",
        "--title",
        "child",
        "--type",
        "task",
        "--parent",
        "Parent -A/Parent -A",
        "--markdown-file",
        str(body),
    )

    assert result.exit_code == 0, result.output
    child = project.vault.load("Parent -A/child/child")
    assert child.markdown == "## Hypothesis\n\nTest it."
    assert child.parent_id == "Parent -A/Parent -A"
    assert (
        invoke(
            "record",
            "create",
            "Parent -A/child/empty/empty",
            "--title",
            "empty",
            "--type",
            "task",
            "--parent",
            "Parent -A/child/child",
        ).exit_code
        == 0
    )
    empty = project.vault.load("Parent -A/child/empty/empty")
    assert empty.markdown == ""
    assert empty.parent_id == child.id


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


def test_mcp_missing_sdk_dependency(tmp_path: Path, monkeypatch) -> None:
    Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    monkeypatch.delitem(sys.modules, "hiraku.mcp", raising=False)

    result = invoke("mcp")

    assert result.exit_code == 1
    assert "MCP SDK dependency" in result.output


def test_example_list_works_outside_a_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = invoke("example", "list")

    assert result.exit_code == 0
    assert "minimal" in result.output
    assert "heart-disease" in result.output
    assert list(tmp_path.iterdir()) == []


def test_init_and_example_create_accept_destination(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    plain = invoke("init", "research")
    example = invoke("example", "create", "minimal", "demo")

    assert plain.exit_code == 0, plain.output
    assert example.exit_code == 0, example.output
    assert Project.load(tmp_path / "research/hiraku.yml").root == (
        tmp_path / "research"
    )
    assert Project.load(tmp_path / "demo/hiraku.yml").root == tmp_path / "demo"
    assert (tmp_path / "demo/evaluate.py").is_file()


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


def test_init_rejects_example_options_and_example_create_requires_example(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    invalid_init = invoke("init", "--example")
    missing_example = invoke("example", "create")

    assert invalid_init.exit_code == 2
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

    rows = json.loads(invoke("runs", "--record", "root/root", "--json").output)
    table = invoke("runs", "--record", "root/root").output

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
    (tmp_path / "runs.csv").write_text("id,record_id,entrypoint\na,root/root,train\n")

    result = invoke("runs", "--record", "root/root", "--json")

    assert result.exit_code == 0
    run = json.loads(result.output)[0]
    assert "entrypoint" not in run
    assert run["raw"]["entrypoint"] == "train"


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
    direct = json.loads(invoke("runs", "--record", "root/root", "--json").output)
    attributed_runs = json.loads(invoke("runs", "--json").output)
    assert {run["id"] for run in direct} == {"a"}
    assert {run["id"] for run in attributed_runs} == {"a", "b"}
    sorted_table = invoke(
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
        if line.startswith("│")
        if (cells := [cell.strip() for cell in line.split("│")[1:-1]])
        and cells[0] != "id"
    ]
    assert [row[0] for row in data_rows] == ["b", "a"]
    assert invoke("runs", "list", "--record", "root/root").exit_code != 0


def test_runs_default_scope_is_all_attributed_runs(tmp_path: Path, monkeypatch) -> None:
    create_tracked_project(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,record_id\na,root/root\nb,root/child/child\nc,\n"
    )
    no_active = invoke("runs", "--json")
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
            "--no-activate",
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
        result = invoke("runs", *args, "--json")
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


def test_results_flag_requires_example(tmp_path: Path):
    result = CliRunner().invoke(main, ["init", str(tmp_path), "--with-results"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def create_tracked_project(*args, **kwargs):
    project = Project.create(*args, **kwargs)
    (project.leaderboards_dir / "default.yml").write_text("source: {type: csv}\n")
    return project


def test_runs_require_view_selection_and_discover_parameters(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    monkeypatch.chdir(tmp_path)
    missing = invoke("runs")
    assert missing.exit_code != 0
    assert "No leaderboards" in missing.output
    for name in ("a", "b"):
        (project.leaderboards_dir / f"{name}.yml").write_text("source: {type: csv}\n")
    ambiguous = invoke("runs")
    assert ambiguous.exit_code != 0
    assert "Select a leaderboard" in ambiguous.output
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.score,param.seed\nr,unknown,0.9,42\n"
    )
    result = invoke("runs", "--leaderboard", "b")
    assert result.exit_code == 0, result.output
    assert "score" in result.output
    assert "seed" in result.output
    assert "42" in result.output
    unknown = invoke("runs", "--leaderboard", "missing")
    assert unknown.exit_code != 0
    assert "Cannot read YAML" in unknown.output


@pytest.fixture
def project(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    monkeypatch.chdir(tmp_path)
    return project


def test_status_reports_project_context_only(project):
    (project.leaderboards_dir / "invalid.yml").write_text("source: [invalid]\n")
    result = invoke("status", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"project_root", "active_record_id", "git_branch"}
    assert data["project_root"] == str(project.root)
    assert data["active_record_id"] is None


def test_leaderboard_lifecycle(project):
    result = invoke("leaderboard", "create", "local", "--source", "csv")
    assert result.exit_code == 0, result.output
    path = project.leaderboards_dir / "local.yml"
    assert path.is_file()
    assert json.loads(invoke("leaderboard", "list", "--json").output) == ["local"]
    shown = invoke("leaderboard", "show", "local", "--json")
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == {"source": {"type": "csv"}}
    source = project.root / "runs.csv"
    source.write_text("id,metric.score\nr,1\n")
    runs = invoke("runs", "--leaderboard", "local", "--include-unattributed", "--json")
    assert runs.exit_code == 0, runs.output
    assert json.loads(runs.output)[0]["metrics"] == {"score": 1}
    before = path.read_bytes()
    duplicate = invoke("leaderboard", "create", "local", "--source", "csv")
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
                "--database",
                "mlflow.db",
                "--experiment-name",
                "test",
            ],
            {"type": "mlflow", "database": "mlflow.db", "experiment_name": "test"},
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
        ["../escape", "--source", "csv"],
        ["bad", "--source", "csv", "--database", "mlflow.db"],
        ["bad", "--source", "wandb"],
        [
            "bad",
            "--source",
            "mlflow",
            "--database",
            "db",
            "--tracking-uri",
            "http://localhost",
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
            "source: {type: csv}\nmetrics: [{name: score, direction: maximize}]\n"
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
