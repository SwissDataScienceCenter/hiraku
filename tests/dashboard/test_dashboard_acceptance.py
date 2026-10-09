from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fixtures import create_acceptance_project, create_launch_project

import hiraku.dashboard.data as data
import hiraku.dashboard.launcher as launcher
from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.capability import DashboardCapability
from hiraku.dashboard.jobs import LaunchController, LaunchRequest
from hiraku.dashboard.plots import pareto_points, progress_points
from hiraku.dashboard.session import DashboardSession
from hiraku.dashboard.views import sorted_runs
from hiraku.record import Record


def _files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_acceptance_fixture_resolves_provenance_without_mutation(
    tmp_path: Path,
) -> None:
    config = create_acceptance_project(tmp_path)
    before = _files(tmp_path)
    active_before = config.active_record_id

    snapshot = build_snapshot(config, record_id="child-a")

    assert _files(tmp_path) == before
    assert HirakuConfig.load(config.path).active_record_id == active_before
    assert {record.id for record in snapshot.records} == {
        "baseline",
        "child-a",
        "child-b",
        "unrelated",
    }
    child = next(record for record in snapshot.records if record.id == "child-a")
    assert child.depth == 1
    assert child.fields["metrics"] == [
        {"name": "acc", "direction": "maximize"},
        {"name": "loss", "direction": "minimize"},
    ]
    assert child.sources["metrics"] == "baseline"
    assert child.fields["tracking_backend"] == {"type": "csv", "path": "results/a.csv"}
    assert child.sources["tracking_backend"] == "child-a"
    assert {run.id for run in snapshot.runs} == {"a-1", "a-missing"}
    missing = next(run for run in snapshot.runs if run.id == "a-missing")
    assert missing.metrics == {}


def test_full_workflow_scope_sort_plot_refresh(tmp_path: Path) -> None:
    config = create_acceptance_project(tmp_path)
    before = _files(tmp_path)
    session = DashboardSession(config)

    first = session.refresh("child-a", "record")
    assert first.snapshot is not None and not first.snapshot.stale
    assert {run.id for run in first.snapshot.runs} == {"a-1", "a-missing"}

    ordered = sorted_runs(first.snapshot, "metric.acc")
    assert [run.id for run in ordered] == ["a-1", "a-missing"]

    progress = progress_points(first.snapshot.runs, "acc", direction="maximize")
    assert list(progress.frame["run_id"]) == ["a-1"]
    assert progress.omitted == 1

    pareto = pareto_points(
        first.snapshot.runs,
        "acc",
        "loss",
        direction_x="maximize",
        direction_y="minimize",
    )
    assert list(pareto.frame["run_id"]) == ["a-1"]
    assert bool(pareto.frame.iloc[0]["is_pareto"]) is True

    source_scope = session.refresh("child-a", "source")
    assert {run.id for run in source_scope.snapshot.runs} == {"a-1", "a-missing"}

    with (tmp_path / "results" / "a.csv").open("a", encoding="utf-8") as stream:
        stream.write("a-2,child-a,2026-01-06T00:00:00Z,0.99,0.01\n")
    refreshed = session.refresh("child-a", "record")
    assert {run.id for run in refreshed.snapshot.runs} == {
        "a-1",
        "a-2",
        "a-missing",
    }
    after = _files(tmp_path)
    assert after != before  # only our explicit CSV append changed
    for path, content in before.items():
        if path.parts[0] == "hiraku":
            assert after[path] == content


def test_local_mlflow_smoke(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "root",
        config=config,
        activate=False,
        tracking_backend={
            "type": "mlflow",
            "database": "mlflow.db",
            "experiment_name": "dashboard-smoke",
        },
    )
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("dashboard-smoke")
    with mlflow.start_run() as active:
        mlflow.set_tag("hiraku.record_id", "root")
        mlflow.log_metric("loss", 0.25)
    run_id = active.info.run_id

    before = _files(tmp_path)

    snapshot = build_snapshot(config, record_id="root")

    assert _files(tmp_path) == before
    assert [run.id for run in snapshot.runs] == [run_id]
    assert snapshot.runs[0].metrics["loss"] == 0.25


def test_wandb_backend_network_access_is_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wandb = pytest.importorskip("wandb")
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "root",
        config=config,
        activate=False,
        tracking_backend={"type": "wandb", "project": "demo"},
    )

    class FakeApi:
        def runs(self, path: str):
            raise AssertionError("W&B network access must be mocked in tests")

    monkeypatch.setattr(wandb, "Api", lambda **kwargs: FakeApi())

    snapshot = build_snapshot(config, record_id="root")

    assert snapshot.runs == ()
    assert any(error.kind == "backend" for error in snapshot.errors)


def test_synthetic_scale_queries_source_once(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    records_dir = config.records_dir
    for index in range(1000):
        record_dir = records_dir / f"rec-{index}"
        record_dir.mkdir(parents=True)
        (record_dir / f"rec-{index}.md").write_text(
            f"---\nid: rec-{index}\n---\n",
            encoding="utf-8",
        )
    target = "rec-0"
    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write("id,record_id,timestamp,metric.score\n")
        for index in range(10000):
            stream.write(f"run-{index},{target},2026-01-01T00:00:00Z,{index}\n")

    original = data._list_source_runs
    calls = 0

    def counting(backend, *, record_id, options):
        nonlocal calls
        calls += 1
        return original(backend, record_id=record_id, options=options)

    monkeypatch.setattr(data, "_list_source_runs", counting)

    started = time.perf_counter()
    snapshot = build_snapshot(config, record_id=target)
    elapsed = time.perf_counter() - started

    assert calls == 1
    assert len(snapshot.records) == 1000
    assert len(snapshot.runs) == 10000
    assert elapsed < 30
    print(f"synthetic 1000-record/10000-run fetch took {elapsed:.2f}s")


def test_local_and_worktree_launch_attribution(tmp_path: Path) -> None:
    config = create_launch_project(tmp_path)
    controller = LaunchController(config, DashboardCapability(interactive=True))

    local = controller.submit(
        LaunchRequest(
            job_id="local-1",
            record_id="child",
            entrypoint="evaluate",
            args={"seed": 1},
        )
    )
    _wait_terminal(controller, local.job_id)

    worktree = controller.submit(
        LaunchRequest(
            job_id="worktree-1",
            record_id="child",
            entrypoint="evaluate",
            args={"seed": 2},
            dispatch="worktree",
        )
    )
    _wait_terminal(controller, worktree.job_id)

    snapshot = build_snapshot(config, record_id="child")
    assert {run.id for run in snapshot.runs} == {"child-1", "child-2"}
    by_id = {run.id: run for run in snapshot.runs}
    assert by_id["child-1"].params["seed"] == "1"
    assert by_id["child-2"].params["seed"] == "2"
    assert by_id["child-1"].entrypoint == "evaluate"
    assert by_id["child-2"].entrypoint == "evaluate"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert by_id["child-1"].commit == head
    assert by_id["child-2"].commit == head
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert worktrees.count("worktree ") == 1


def _wait_terminal(controller: LaunchController, job_id: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        views = controller.poll()
        job = next((view for view in views if view.job_id == job_id), None)
        if job and job.state in {"succeeded", "failed"}:
            assert job.state == "succeeded", job.error
            return
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish")


def test_packaged_notebook_renders_with_marimo(tmp_path: Path) -> None:
    config = create_acceptance_project(tmp_path)
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    environment = launcher.dashboard_environment(config, record_id="child-a")
    environment["HOME"] = str(home)
    environment["TMPDIR"] = str(temp)
    output = tmp_path / "rendered.html"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marimo",
            "export",
            "html",
            str(launcher.default_notebook_path()),
            "-o",
            str(output),
        ],
        cwd=tmp_path,
        env={**os.environ, **environment},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    assert "Hiraku dashboard" in rendered
