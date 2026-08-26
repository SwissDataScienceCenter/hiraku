from __future__ import annotations

import sys
from typing import Any

import pytest

from hiraku.agent import PROTOCOL_NAME, PROTOCOL_VERSION, AgentRequest, ProcessAgentAdapter
from hiraku.auto import AgentResearchService, AutoService, parse_duration
from hiraku.errors import HirakuError


class FakeResearchAdapter:
    def __init__(self):
        self.requests: list[AgentRequest] = []

    def exchange(self, request: AgentRequest) -> dict[str, Any]:
        self.requests.append(request)
        if request.action == "propose":
            payload = {
                "decision": "propose",
                "experiment_id": "agent-candidate",
                "hypothesis": "The candidate improves score over the immutable baseline.",
                "parent_experiment_id": None,
            }
        elif request.action == "implement":
            root = request.project_root
            from pathlib import Path

            Path(root, "agent-change.txt").write_text("agent implementation\n", encoding="utf-8")
            payload = {"status": "implemented", "summary": "Added the candidate implementation."}
        else:
            payload = {
                "verdict": request.instructions["computed_verdict"],
                "rationale": "The candidate score is greater than the baseline score.",
                "continue": False,
            }
        return {
            "request_id": request.request_id,
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "action": request.action,
            "payload": payload,
        }


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
    result = AutoService(app).loop("demo", max_experiments=2, max_runs=2, time_budget=parse_duration("30s"))
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
        AutoService(app).loop("demo", max_experiments=2, max_runs=2, time_budget=parse_duration("30s"))


def test_agent_loop_uses_neutral_protocol_and_shared_core(project):
    root, app = project
    adapter = FakeResearchAdapter()
    result = AgentResearchService(app, adapter).loop(
        "demo",
        max_experiments=2,
        max_runs=2,
        time_budget=parse_duration("30s"),
    )

    assert result == {
        "goal_id": "demo",
        "experiments_created": 2,
        "runs_launched": 2,
        "failures": 0,
        "stop_reason": "agent",
        "driver": "agent-protocol",
    }
    assert [request.action for request in adapter.requests] == ["propose", "implement", "analyze"]
    experiment = app.experiments.get("demo/agent-candidate")
    assert experiment.origin == "auto"
    assert experiment.status == "concluded"
    assert experiment.verdict == "supports"
    assert len(app.runs.rows("demo", "agent-candidate")) == 1
    assert not (root / "auto-implementation.txt").exists()
    analysis = [event for event in app.events.list("demo/agent-candidate") if event["event_type"] == "agent_analysis"]
    assert analysis[0]["payload"]["rationale"].startswith("The candidate score")


def test_skill_driven_agent_primitives_use_same_services(project):
    root, app = project
    auto = AutoService(app)
    baseline = auto.baseline("demo")
    assert baseline["created"] is True
    assert auto.baseline("demo")["created"] is False
    proposal = auto.propose("demo", "skill-candidate", "A skill-aware agent can improve score.")
    experiment = auto.next("demo")
    assert proposal["status"] == "pending"
    assert experiment["id"] == "skill-candidate"

    (root / "skill-change.txt").write_text("implemented\n", encoding="utf-8")
    completed = auto.complete_implementation("demo/skill-candidate", "Implemented through agent tools.")
    assert completed["status"] == "implementing"
    app.launcher.launch("demo/skill-candidate:evaluate")
    analyzed = auto.analyze("demo/skill-candidate")
    assert analyzed["verdict"] == "supports"


def test_agent_cannot_commit_hiraku_control_file_changes(project):
    root, app = project
    auto = AutoService(app)
    auto.propose("demo", "unsafe", "Changing control state would appear to improve the score.")
    auto.next("demo")
    config = root / ".hiraku.yml"
    config.write_text(config.read_text(encoding="utf-8") + "\nunsafe: true\n", encoding="utf-8")
    with pytest.raises(HirakuError, match="protected Hiraku control files"):
        auto.complete_implementation("demo/unsafe")


def test_process_adapter_drives_complete_research_cycle(project):
    root, app = project
    script = root.parent / "runtime-adapter.py"
    script.write_text(
        """import json, sys
from pathlib import Path
r = json.load(sys.stdin)
if r["action"] == "propose":
    payload = {"decision": "propose", "experiment_id": "process-candidate",
               "hypothesis": "The process candidate improves score.", "parent_experiment_id": None}
elif r["action"] == "implement":
    Path(r["project_root"], "process-change.txt").write_text("implemented\\n")
    payload = {"status": "implemented", "summary": "Implemented through the protocol."}
else:
    payload = {"verdict": r["instructions"]["computed_verdict"],
               "rationale": "Recorded evidence improved.", "continue": False}
json.dump({"request_id": r["request_id"], "protocol": r["protocol"], "version": r["version"],
           "action": r["action"], "payload": payload}, sys.stdout)
""",
        encoding="utf-8",
    )
    result = AgentResearchService(
        app,
        ProcessAgentAdapter([sys.executable, str(script)]),
    ).loop("demo", max_experiments=2, max_runs=2, time_budget=parse_duration("30s"))

    assert result["failures"] == 0
    assert result["driver"] == "agent-protocol"
    assert app.experiments.get("demo/process-candidate").verdict == "supports"
