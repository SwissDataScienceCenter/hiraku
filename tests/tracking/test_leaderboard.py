from pathlib import Path

import psij
import pytest

from hiraku.errors import BackendError, ExecutionError, ValidationError
from hiraku.jobs import Job
from hiraku.project import Project
from hiraku.tracking import CSVBackend, Leaderboard, MLflowBackend, WandbBackend


@pytest.mark.parametrize("launch_commit", [None, "launch-commit"])
def test_job_join_preserves_trace_commit_and_queries_after_attribution(
    tmp_path, monkeypatch, launch_commit
):
    project = Project(tmp_path)
    job = Job(
        id="a" * 32,
        record_id="selected",
        commit=launch_commit,
        spec=psij.JobSpec(executable="echo"),
    )
    directory = tmp_path / ".hiraku/jobs" / job.id
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(job.model_dump_json())
    (tmp_path / "runs.csv").write_text(
        "id,hiraku_job_id,record_id,commit,metric.score\n"
        f"one,{job.id},wrong,trace-commit,2\n"
        f"two,{job.id},,trace-commit,1\n"
        f"missing,{'b' * 32},fallback,source-commit,0\n"
        "unknown,unknown,,,0\n"
    )
    loads = []
    original = Job.load

    def load(job_id, **kwargs):
        loads.append(job_id)
        return original(job_id, **kwargs)

    monkeypatch.setattr(Job, "load", load)
    board = Leaderboard(source={"type": "csv", "path": "runs.csv"})
    runs = board.list_runs(project, record_id="selected", sort="score", limit=1)
    assert [run.id for run in runs] == ["two"]
    assert runs[0].commit == "trace-commit"
    assert "launch_commit" not in runs[0].model_dump(include={"launch_commit"})
    assert original(runs[0].job_id, project=project).commit == launch_commit
    assert loads.count(job.id) == 1
    source = CSVBackend(project, path="runs.csv").read_runs()
    assert source[0].record_id == "wrong"
    missing = board.list_runs(project, record_id="fallback")[0]
    assert missing.commit == "source-commit"
    assert len(board.list_runs(project)) == 3
    assert len(board.list_runs(project, include_unattributed=True)) == 4
    (directory / "job.json").write_text("{")
    with pytest.raises(ExecutionError, match="Cannot read job"):
        board.list_runs(project)


@pytest.mark.parametrize(
    "query",
    [
        {"record_id": ""},
        {"record_id": 1},
        {"include_unattributed": "yes"},
        {"sort": ""},
        {"ascending": True},
        {"sort": "score", "ascending": "yes"},
        {"limit": -1},
        {"limit": True},
    ],
)
def test_leaderboard_validates_before_reading_source(tmp_path, monkeypatch, query):
    monkeypatch.setattr(
        CSVBackend, "read_runs", lambda self: pytest.fail("Source must not be read")
    )
    board = Leaderboard(source={"type": "csv", "path": "runs.csv"})
    with pytest.raises(BackendError):
        board.list_runs(Project(tmp_path), **query)


def test_discovery_and_selection_are_independent_of_records(tmp_path: Path):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    with pytest.raises(ValidationError, match="No leaderboards"):
        Leaderboard.load(project)
    (project.leaderboards_dir / "first.yml").write_text(
        "source: {type: csv, path: runs.csv}\n"
    )
    assert Leaderboard.list(project) == ["first"]
    assert Leaderboard.load(project).source.type == "csv"
    (project.leaderboards_dir / "broken.yml").write_text("source: [invalid]\n")
    assert Leaderboard.list(project) == ["broken", "first"]
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
        {"source": {"type": "csv", "path": "runs.csv", "unexpected": 1}},
        {"source": {"type": "csv", "path": "runs.csv"}, "metrics": [{"name": " "}]},
        {
            "source": {"type": "csv", "path": "runs.csv"},
            "metrics": [{"name": "a"}, {"name": "a"}],
        },
        {
            "source": {"type": "csv", "path": "runs.csv"},
            "metrics": [{"name": "a", "direction": "up"}],
        },
        {
            "source": {"type": "csv", "path": "runs.csv"},
            "metrics": [{"name": "a", "unit": "s"}],
        },
    ],
)
def test_invalid_leaderboard_configuration(config):
    with pytest.raises(ValueError):
        Leaderboard.model_validate(config)


@pytest.mark.parametrize("absolute", [False, True])
def test_csv_environment_path_resolves_without_rewriting_config(
    tmp_path, monkeypatch, absolute
):
    project = Project(tmp_path / "project")
    directory = tmp_path / "external" if absolute else project.root / "results"
    directory.mkdir(parents=True)
    (directory / "runs.csv").write_text("id,record_id\nrun,record\n")
    expression = "${HIRAKU_TEST_RESULTS}/runs.csv"
    monkeypatch.delenv("HIRAKU_TEST_RESULTS", raising=False)
    Leaderboard.create(project, "local", {"type": "csv", "path": expression})
    path = project.leaderboards_dir / "local.yml"
    before = path.read_bytes()
    leaderboard = Leaderboard.load(project)
    assert str(leaderboard.source.path) == expression

    monkeypatch.setenv("HIRAKU_TEST_RESULTS", str(directory) if absolute else "results")
    monkeypatch.chdir(tmp_path)

    assert [run.id for run in leaderboard.list_runs(project)] == ["run"]
    assert path.read_bytes() == before
    assert str(leaderboard.source.path) == expression


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
    assert [
        run.id for run in leaderboard.list_runs(project, sort="accuracy", limit=1)
    ] == ["a"]
    runs = leaderboard.list_runs(project, sort="accuracy", include_unattributed=True)
    assert [run.id for run in runs] == ["b", "a"]
    assert runs[0].record_id is None
    assert runs[0].params == {"accuracy": "low"}
    assert leaderboard.metric_names(runs) == ["missing", "accuracy"]
    assert [run.id for run in leaderboard.list_runs(project, record_id="unknown")] == [
        "a"
    ]
    assert leaderboard.list_runs(project, record_id="absent") == []
    path.write_text("id,metric.new,param.seed\nc,1,42\n")
    assert leaderboard.list_runs(project) == []
    runs = leaderboard.list_runs(project, include_unattributed=True)
    assert runs[0].metrics == {"new": 1}
    assert leaderboard.metric_names(runs) == ["missing", "accuracy"]
    discovered = Leaderboard(source={"type": "csv", "path": "results.csv"})
    assert discovered.metric_names(runs) == ["new"]
    assert (
        Leaderboard(
            source={"type": "csv", "path": "runs.csv"}, metrics=[]
        ).metric_names(runs)
        == []
    )


def test_metric_sort_does_not_collide_with_metadata_or_parameters(tmp_path: Path):
    project = Project(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,duration,metric.duration,param.duration,param.seed,metric.metric.score\n"
        "a,unknown,1,9,1,2,0\nb,unknown,9,1,9,1,1\nc,unknown,2,,0,3,0.5\n"
    )
    leaderboard = Leaderboard(source={"type": "csv", "path": "runs.csv"})
    assert [run.id for run in leaderboard.list_runs(project, sort="duration")] == [
        "b",
        "a",
        "c",
    ]
    assert [
        run.id for run in leaderboard.list_runs(project, sort="duration", limit=1)
    ] == ["b"]
    assert [
        run.id for run in CSVBackend(project, path="runs.csv").list_runs(sort="seed")
    ] == [
        "a",
        "b",
        "c",
    ]
    assert [run.id for run in leaderboard.list_runs(project, sort="metric.score")] == [
        "a",
        "c",
        "b",
    ]
    with pytest.raises(BackendError, match="requires a sort"):
        leaderboard.list_runs(project, ascending=True)
    with pytest.raises(BackendError, match="boolean"):
        leaderboard.list_runs(project, include_unattributed="yes")
    with pytest.raises(BackendError, match="metric name"):
        leaderboard.list_runs(project, sort="")
    with pytest.raises(BackendError, match="non-negative"):
        leaderboard.list_runs(project, limit=-1)
