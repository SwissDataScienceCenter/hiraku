from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ...app import HirakuApp


def create_goal(
    application: HirakuApp,
    goal_id: str,
    metric: str,
    direction: str,
    launch: Path,
    *,
    title: str | None = None,
    objective: str = "",
    target: float | None = None,
    protocol: str = "",
    constraints: Iterable[str] = (),
):
    return application.goals.create(
        goal_id,
        metric,
        direction,
        launch,
        title=title,
        objective=objective,
        target=target,
        evaluation_protocol=protocol,
        constraints=constraints,
    )


def list_goals(application: HirakuApp):
    return application.goals.list()


def show_goal(application: HirakuApp, goal_id: str):
    return application.goals.get(goal_id)
