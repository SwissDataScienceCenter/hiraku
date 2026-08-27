from __future__ import annotations

from datetime import timedelta
from typing import Any

from ...app import HirakuApp
from ...orchestration.autonomous import AgentResearchService, AutoService


def run_auto_loop(
    application: HirakuApp,
    goal_id: str,
    *,
    max_experiments: int,
    max_runs: int,
    time_budget: timedelta,
    adapter: Any = None,
) -> dict[str, Any]:
    service = AgentResearchService(application, adapter) if adapter else AutoService(application)
    return service.loop(
        goal_id,
        max_experiments=max_experiments,
        max_runs=max_runs,
        time_budget=time_budget,
    )
