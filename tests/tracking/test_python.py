import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import BackendError, ValidationError
from hiraku.project import Project
from hiraku.tracking import Leaderboard


def definition(project, body):
    path = project.leaderboards_dir / "custom.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_python_discovery_does_not_execute_and_duplicate_names_fail(tmp_path):
    project = Project(tmp_path)
    definition(project, "raise RuntimeError('not executed during discovery')")
    assert Leaderboard.list(project) == ["custom"]
    with pytest.raises(ValidationError, match="not executed"):
        Leaderboard.load(project, "custom")
    (project.leaderboards_dir / "custom.yml").write_text("source: {}")
    with pytest.raises(ValidationError, match="Duplicate leaderboard"):
        Leaderboard.list(project)
    with pytest.raises(ValidationError, match="Duplicate leaderboard"):
        Leaderboard.load(project, "custom")


def test_python_queries_metrics_and_reload(tmp_path):
    project = Project(tmp_path)
    path = definition(
        project,
        "from hiraku.tracking import Metric, Run\n"
        "metrics = [Metric(name='score', direction='maximize')]\n"
        "def load_runs(project):\n"
        "    return [Run(id='a', record_id='task', metrics={'score': 1}),\n"
        "            Run(id='b', record_id='task', metrics={'score': 2}),\n"
        "            Run(id='c', metrics={'score': 3})]\n",
    )
    board = Leaderboard.load(project)
    assert board.source.type == "python"
    assert board.metric_names([]) == ["score"]
    assert [r.id for r in board.list_runs(project, sort="score", limit=1)] == ["b"]
    assert len(board.list_runs(project, include_unattributed=True)) == 3
    assert board.list_runs(project, record_id="other") == []
    path.write_text("def load_runs(project):\n    return []\n")
    assert board.list_runs(project) == []
    with pytest.raises(ValidationError, match="already exists"):
        Leaderboard.create(project, "custom", {"type": "csv", "path": "runs.csv"})


@pytest.mark.parametrize(
    "body, error",
    [
        ("metrics = []", "callable load_runs"),
        ("def load_runs(project): return [{}]", "list\\[Run\\]"),
        ("def load_runs(project): raise ValueError('broken')", "broken"),
        (
            "from hiraku.tracking import Run\n"
            "def load_runs(project): return [Run(id='a'), Run(id='a')]",
            "unique",
        ),
    ],
)
def test_python_errors_include_definition_path(tmp_path, body, error):
    project = Project(tmp_path)
    path = definition(project, body)
    with pytest.raises((BackendError, ValidationError), match=error) as exc:
        Leaderboard.load(project).list_runs(project)
    assert str(path) in str(exc.value)


def test_python_cli_show_edit_delete(tmp_path, monkeypatch):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    path = definition(project, "def load_runs(project): return []\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    shown = runner.invoke(main, ["leaderboard", "show", "custom", "--json"])
    assert shown.exit_code == 0, shown.output
    assert '"type": "python"' in shown.output
    edited = []
    monkeypatch.setattr("hiraku.cli._edit", lambda p: edited.append(p))
    assert runner.invoke(main, ["leaderboard", "edit", "custom"]).exit_code == 0
    assert edited == [path]
    assert runner.invoke(main, ["leaderboard", "delete", "custom"]).exit_code == 0
    assert not path.exists()


def test_yaml_can_select_python_source(tmp_path):
    project = Project(tmp_path)
    helper = tmp_path / "reader.py"
    helper.write_text("def load_runs(project): return []\n")
    Leaderboard.create(project, "custom", {"type": "python", "path": "reader.py"})
    assert Leaderboard.load(project).list_runs(project) == []
