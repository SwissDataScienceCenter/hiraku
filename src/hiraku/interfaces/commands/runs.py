from __future__ import annotations

from ...app import HirakuApp


def list_runs(application: HirakuApp, goal_id: str | None = None, experiment_id: str | None = None):
    return application.runs.rows(goal_id, experiment_id)


def log_run(application: HirakuApp, run_id: str, parameters: dict[str, str], metrics: dict[str, str]) -> None:
    application.runs.log(run_id, parameters, metrics)
