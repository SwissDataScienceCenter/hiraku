from __future__ import annotations

import json

import pytest

from hiraku.errors import HirakuError


def test_launch_records_success_metrics_and_provenance(project):
    _, app = project
    app.experiments.create("demo/baseline", "reference", kind="baseline")
    run_id, code = app.launcher.launch("demo/baseline:evaluate")
    assert code == 0
    row = app.runs.rows("demo", "baseline")[0]
    assert row["id"] == run_id
    assert row["status"] == "completed"
    assert row["score"] == "0.75"
    assert row["param/seed"] == "42"
    assert row["provenance"]["python"]


def test_failed_launch_is_durable(project):
    _, app = project
    app.experiments.create("demo/failure", "failure")
    run_id, code = app.launcher.launch("demo/failure:fail")
    assert code == 9
    row = app.runs.rows("demo", "failure")[0]
    assert row["id"] == run_id
    assert row["status"] == "failed"


def test_signal_and_missing_metrics_are_durable(project):
    _, app = project
    app.experiments.create("demo/signaled", "signaled")
    _, code = app.launcher.launch("demo/signaled:killed")
    assert code < 0
    assert app.runs.rows("demo", "signaled")[0]["status"] == "killed"
    app.experiments.create("demo/no-metrics", "no metrics")
    _, code = app.launcher.launch("demo/no-metrics:no-metrics")
    row = app.runs.rows("demo", "no-metrics")[0]
    assert code == 0
    assert row["status"] == "completed"
    assert "score" not in row


def test_commit_launch_uses_temporary_detached_worktree(project):
    _, app = project
    experiment = app.experiments.create("demo/reproducible", "reproducible")
    branch = app.git.current_branch()
    _, code = app.launcher.launch("demo/reproducible:evaluate", experiment.parent_commit)
    assert code == 0
    assert app.git.current_branch() == branch
    assert not app.git._run("worktree", "list", "--porcelain").stdout.count("hiraku-run-")


def test_detached_child_cli_logs_to_branch_independent_state(project):
    _, app = project
    experiment = app.experiments.create("demo/cli-log", "cli log")
    run_id, code = app.launcher.launch("demo/cli-log:cli-log", experiment.parent_commit)
    assert code == 0
    row = next(row for row in app.runs.rows("demo", "cli-log") if row["id"] == run_id)
    assert row["param/seed"] == "7"
    assert row["score"] == "0.7"


def test_startup_failure_is_durable(project):
    _, app = project
    goal = app.goals.get("demo")
    manifest = goal.launch_manifest
    manifest["entrypoints"]["missing"] = {
        "command": ["definitely-not-a-program"],
        "cwd": ".",
        "env": {},
    }
    with app.db.transaction() as connection:
        connection.execute("UPDATE goals SET launch_manifest_json=? WHERE id='demo'", (json.dumps(manifest),))
    app.experiments.create("demo/startup", "startup")
    with pytest.raises(HirakuError, match="Could not start"):
        app.launcher.launch("demo/startup:missing")
    assert app.runs.rows("demo", "startup")[0]["status"] == "failed"


def test_parameters_immutable_metrics_update(project):
    _, app = project
    experiment = app.experiments.create("demo/logging", "logging")
    run_id = app.runs.create(experiment, app.git.head_commit(), "evaluate", {})
    app.runs.log(run_id, {"seed": "1"}, {"score": "0.1"})
    app.runs.log(run_id, {"seed": "1"}, {"score": "0.2"})
    with pytest.raises(HirakuError, match="immutable"):
        app.runs.log(run_id, {"seed": "2"}, {})
    assert app.runs.rows("demo", "logging")[0]["score"] == "0.2"


def test_stable_numeric_sort_and_unranked(project):
    _, app = project
    experiment = app.experiments.create("demo/sort", "sort")
    for value in ("2", "invalid", "1", "2"):
        run_id = app.runs.create(experiment, app.git.head_commit(), "evaluate", {})
        app.runs.log(run_id, {}, {"score": value})
        app.runs.finalize(run_id, "completed", 0)
    rows = app.runs.sort_rows(app.runs.rows("demo", "sort"), "score", True)
    assert [row["score"] for row in rows] == ["2", "2", "1", "invalid"]
    assert [row["_rank"] for row in rows] == [1, 2, 3, None]


def test_reports_match_state(project):
    _, app = project
    app.experiments.create("demo/report", "report")
    app.launcher.launch("demo/report:evaluate")
    markdown = app.reports.export_markdown("demo")
    json_path = app.reports.export_json("demo")
    csv_path = app.reports.export_csv("demo")
    assert "GENERATED PROJECTION" in markdown.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["runs"][0]["experiment_id"] == "report"
    assert "experiment_id" in csv_path.read_text(encoding="utf-8")
