from pathlib import Path

import pytest

from hiraku.errors import BackendError, ValidationError
from hiraku.project import Project
from hiraku.tracking import CSVBackend, Leaderboard, MLflowBackend, WandbBackend


def test_discovery_and_selection_are_independent_of_records(tmp_path: Path):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    with pytest.raises(ValidationError, match="No leaderboards"):
        Leaderboard.load(project)
    (project.leaderboards_dir / "first.yml").write_text("source: {type: csv}\n")
    assert Leaderboard.discover(project) == ["first"]
    assert Leaderboard.load(project).source.type == "csv"
    (project.leaderboards_dir / "broken.yml").write_text("source: [invalid]\n")
    assert Leaderboard.discover(project) == ["broken", "first"]
    with pytest.raises(ValidationError, match="Select a leaderboard"):
        Leaderboard.load(project)
    assert Leaderboard.load(project, "first").source.type == "csv"
    with pytest.raises(ValidationError, match="Invalid leaderboard"):
        Leaderboard.load(project, "broken")
    with pytest.raises(ValidationError, match="Cannot read YAML"):
        Leaderboard.load(project, "missing")


@pytest.mark.parametrize("name", ["../escape", "nested/leaderboard", "", ".", ".."])
def test_leaderboard_names_cannot_traverse_directories(tmp_path: Path, name: str):
    with pytest.raises(ValidationError, match="leaderboard name"):
        Leaderboard.load(Project(tmp_path), name)


@pytest.mark.parametrize(
    "config",
    [
        {"source": {"type": "unknown"}},
        {"source": {"type": "csv", "unexpected": 1}},
        {"source": {"type": "csv"}, "metrics": [{"name": " "}]},
        {"source": {"type": "csv"}, "metrics": [{"name": "a"}, {"name": "a"}]},
        {"source": {"type": "csv"}, "metrics": [{"name": "a", "direction": "up"}]},
        {"source": {"type": "csv"}, "metrics": [{"name": "a", "unit": "s"}]},
    ],
)
def test_invalid_leaderboard_configuration(config):
    with pytest.raises(ValueError):
        Leaderboard.model_validate(config)


@pytest.mark.parametrize(
    "source, adapter, attribute, expected",
    [
        (
            {"type": "csv", "path": "results.csv"},
            CSVBackend,
            "path",
            Path("results.csv"),
        ),
        (
            {"type": "mlflow", "experiment_name": "evaluation"},
            MLflowBackend,
            "experiment_name",
            "evaluation",
        ),
        (
            {"type": "wandb", "project": "evaluation"},
            WandbBackend,
            "project",
            "evaluation",
        ),
    ],
)
def test_source_selects_configured_adapter(
    tmp_path, monkeypatch, source, adapter, attribute, expected
):
    monkeypatch.setattr(WandbBackend, "_get_api", lambda self: object())
    backend = Leaderboard(source=source)._create_backend(Project(tmp_path))
    assert isinstance(backend, adapter)
    assert getattr(backend, attribute) == (
        tmp_path / expected if isinstance(expected, Path) else expected
    )


def test_leaderboard_tolerates_schema_changes_and_keeps_full_runs(tmp_path: Path):
    project = Project(tmp_path)
    path = tmp_path / "results.csv"
    path.write_text(
        "id,record_id,metric.accuracy,param.accuracy\na,unknown,0.5,high\nb,,0.9,low\n"
    )
    leaderboard = Leaderboard.model_validate(
        {
            "source": {"type": "csv", "path": "results.csv"},
            "metrics": [
                {"name": "missing"},
                {"name": "accuracy", "direction": "maximize"},
            ],
        }
    )
    runs = leaderboard.list_runs(project, sort="accuracy")
    assert [run.id for run in runs] == ["b", "a"]
    assert runs[0].record_id is None
    assert runs[0].params == {"accuracy": "low"}
    assert leaderboard.metric_names(runs) == ["missing", "accuracy"]
    assert [run.id for run in leaderboard.list_runs(project, record_id="unknown")] == [
        "a"
    ]
    assert leaderboard.list_runs(project, record_id="absent") == []
    path.write_text("id,metric.new,param.seed\nc,1,42\n")
    runs = leaderboard.list_runs(project)
    assert runs[0].metrics == {"new": 1}
    assert leaderboard.metric_names(runs) == ["missing", "accuracy"]
    discovered = Leaderboard(source={"type": "csv", "path": "results.csv"})
    assert discovered.metric_names(runs) == ["new"]
    assert Leaderboard(source={"type": "csv"}, metrics=[]).metric_names(runs) == []


def test_metric_sort_does_not_collide_with_metadata_or_parameters(tmp_path: Path):
    project = Project(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,duration,metric.duration,param.duration\na,1,9,1\nb,9,1,9\nc,2,,0\n"
    )
    leaderboard = Leaderboard(source={"type": "csv"})
    assert [run.id for run in leaderboard.list_runs(project, sort="duration")] == [
        "b",
        "a",
        "c",
    ]
    assert [
        run.id for run in leaderboard.list_runs(project, sort="duration", limit=1)
    ] == ["b"]
    with pytest.raises(BackendError, match="requires a sort"):
        leaderboard.list_runs(project, ascending=True)
    with pytest.raises(BackendError, match="metric name"):
        leaderboard.list_runs(project, sort="")
    with pytest.raises(BackendError, match="non-negative"):
        leaderboard.list_runs(project, limit=-1)
