from __future__ import annotations

import pytest

from hiraku.auto import AutoService, parse_duration
from hiraku.errors import HirakuError


def test_auto_proposal_becomes_ordinary_experiment(project):
    _, app = project
    auto = AutoService(app)
    proposal = auto.discover("demo")
    experiment = auto.next("demo")
    assert experiment["origin"] == "auto"
    assert experiment["goal_id"] == "demo"
    assert auto.get_proposal(proposal["id"])["status"] == "admitted"


def test_auto_loop_obeys_bounds_and_uses_shared_runs(project):
    _, app = project
    result = AutoService(app).loop(
        "demo", max_experiments=2, max_runs=2, time_budget=parse_duration("30s")
    )
    assert result["experiments_created"] == 2
    assert result["runs_launched"] == 2
    experiments = app.experiments.list("demo")
    baseline = next(item for item in experiments if item.kind == "baseline")
    experiment = next(item for item in experiments if item.kind == "hypothesis")
    assert experiment.origin == "auto"
    assert experiment.status == "concluded"
    assert experiment.verdict == "supports"
    assert experiment.evaluated_commit != baseline.evaluated_commit
    assert len(app.runs.rows("demo", experiment.id)) == 1


def test_auto_requires_an_evaluated_existing_baseline(project):
    _, app = project
    app.experiments.create("demo/baseline", "baseline", kind="baseline")
    with pytest.raises(HirakuError, match="must be evaluated"):
        AutoService(app).loop(
            "demo", max_experiments=2, max_runs=2, time_budget=parse_duration("30s")
        )
