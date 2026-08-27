from __future__ import annotations

from ...app import HirakuApp


def create_experiment(
    application: HirakuApp,
    reference: str,
    hypothesis: str,
    *,
    kind: str = "hypothesis",
    parent: str | None = None,
):
    return application.experiments.create(reference, hypothesis, kind=kind, parent=parent, origin="guided")


def list_experiments(application: HirakuApp, goal_id: str | None = None):
    return application.experiments.list(goal_id)


def show_experiment(application: HirakuApp, reference: str):
    return application.experiments.get(reference)
