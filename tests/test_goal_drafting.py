from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.auto import AgentResearchService
from hiraku.context import collect_project_context
from hiraku.errors import HirakuError


class DraftAdapter:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request = None

    def exchange(self, request):
        self.request = request
        return {
            "request_id": request.request_id,
            "protocol": request.protocol,
            "version": request.version,
            "action": "draft_goal",
            "payload": self.payload,
        }


def draft_payload() -> dict:
    return {
        "goal_id": "new-goal",
        "title": "Improve score",
        "objective": "Improve the validation score.",
        "metric": "score",
        "direction": "max",
        "target": 0.9,
        "evaluation_protocol": "Run the deterministic evaluator.",
        "constraints": ["Use the provided split."],
        "unset": [],
        "rationale": "The evaluator and score are present in the project.",
    }


def test_context_is_bounded_and_excludes_state_and_secrets(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".hiraku").mkdir()
    (tmp_path / ".hiraku" / "state.db").write_bytes(b"not for agents")
    context = collect_project_context(tmp_path, max_files=1, max_bytes=10)
    assert context["file_count"] == 1
    assert context["files"][0]["path"] == "README.md"
    assert "secret" not in json.dumps(context)


def test_goal_draft_is_review_only_until_applied(project):
    root, application = project
    (root / "README.md").write_text("Project goal context", encoding="utf-8")
    adapter = DraftAdapter(draft_payload())
    result = AgentResearchService(application, adapter).draft_goal("new-goal", "maximize the score")
    assert result["applied"] is False
    assert result["draft"]["metric"] == "score"
    with pytest.raises(HirakuError, match="does not exist"):
        application.goals.get("new-goal")
    assert adapter.request.state["project_context"]["file_count"] > 0


def test_goal_draft_can_be_explicitly_applied(project):
    root, application = project
    adapter = DraftAdapter(draft_payload())
    result = AgentResearchService(application, adapter).draft_goal(
        "new-goal", "maximize the score", launch_path=root / "launch.yml", apply=True
    )
    assert result["applied"] is True
    assert application.goals.get("new-goal").metric == "score"


def test_incomplete_goal_draft_cannot_be_applied(project):
    root, application = project
    payload = draft_payload()
    payload["metric"] = None
    adapter = DraftAdapter(payload)
    with pytest.raises(HirakuError, match="incomplete"):
        AgentResearchService(application, adapter).draft_goal(
            "new-goal", "maximize the score", launch_path=root / "launch.yml", apply=True
        )
