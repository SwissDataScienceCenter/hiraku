"""Tests for `hiraku_agent.loop`: driving a bounded research loop from a `Program`
record (Phase 1 of `hiraku/program-action-merge-proposal-zou.md`).

No real LLM is called anywhere — see `test_hiraku_agent_actions.py`'s module
docstring for why an `Action`'s `agent_cmd` here points at a tiny on-disk script
rather than the `stub_agent()` argv-list trick `test_hiraku_agent_workflows.py` uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.agent import Action, Program
from hiraku.config import HirakuConfig
from hiraku.data import Run, Task
from hiraku_agent.errors import AgentError
from hiraku_agent.loop import _duration_seconds, run_loop
from hiraku_agent.node import Node, create_node, load_node


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Task:
    return Task.create(
        "vo-backend",
        config=config,
        goal="Improve validation accuracy",
        entrypoints={"train": {"command": ["python", "train.py"]}},
        metrics=[{"name": "val_accuracy", "direction": "maximize"}],
    )


def _save_action(
    config: HirakuConfig, action_id: str, entity: str, **fields: object
) -> Action:
    action = Action(id=action_id, entity=entity, markdown="", **fields)
    action.save(config.actions_dir / entity / action_id / "action.md")
    return action


def _save_program(config: HirakuConfig, program_id: str, **fields: object) -> Program:
    program = Program(id=program_id, markdown="", **fields)
    program.save(config.programs_dir / program_id / "program.md")
    return program


def _stub_script(tmp_path: Path, payload: dict) -> Path:
    script = tmp_path / "stub_agent.py"
    script.write_text(f"print({json.dumps(json.dumps(payload))})\n")
    return script


# ---------------------------------------------------------------- _duration_seconds


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2h", 7200.0),
        ("90m", 5400.0),
        ("45s", 45.0),
        ("120", 120.0),
        (120, 120.0),
        (None, None),
        ("", None),
        ("not-a-duration", None),
        (True, None),  # a bool is an int, but not a meaningful duration
    ],
)
def test_duration_seconds(value: object, expected: float | None) -> None:
    assert _duration_seconds(value) == expected


# ---------------------------------------------------------------------- run_loop


def test_run_loop_runs_one_step_then_stops_for_approval(
    config: HirakuConfig, task: Task, tmp_path: Path
) -> None:
    payload = {
        "experiments": [{"id": "baseline", "kind": "baseline", "expectation": "x"}],
        "notes": None,
    }
    script = _stub_script(tmp_path, payload)
    _save_action(
        config,
        "auto-design",
        "vo-backend",
        workflow="design",
        agent_cmd=f"python3 {script}",
    )
    _save_program(config, "auto-loop", actions=["auto-design"])

    result = run_loop("auto-loop", "vo-backend", config=config)

    assert [step.action for step in result.steps] == ["design"]
    assert result.steps[0].action_id == "auto-design"
    assert "approve" in result.stopped
    assert load_node("baseline", task=task, config=config).id == "baseline"


def test_run_loop_calls_analyze_for_a_node_awaiting_analysis(
    config: HirakuConfig,
    task: Task,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = create_node(
        "baseline", task=task, kind="baseline", expectation="x", config=config
    )
    node.commit = "a" * 40
    node.save()
    stub_run = Run(
        id="r1",
        metrics={"val_accuracy": 0.83},
        params={},
        task_id="vo-backend",
        experiment_id="baseline",
        commit="a" * 40,
        timestamp="2026-09-18T00:00:00Z",
        entrypoint="train",
        url="",
        raw={},
    )
    monkeypatch.setattr(Node, "runs", lambda self, **_: [stub_run])

    payload = {
        "verdict": "supports",
        "findings": "0.83, above target",
        "conclusions": "met",
        "next_steps": None,
    }
    script = _stub_script(tmp_path, payload)
    _save_action(
        config,
        "auto-analyze",
        "vo-backend",
        workflow="analyze",
        agent_cmd=f"python3 {script}",
    )
    _save_program(config, "auto-loop", actions=["auto-analyze"])

    result = run_loop("auto-loop", "vo-backend", config=config)

    assert len(result.steps) == 1
    assert result.steps[0].action == "analyze"
    assert result.steps[0].node == "baseline"
    reloaded = load_node("baseline", task=task, config=config)
    assert reloaded.verdict == "supports"
    assert result.stopped == "nothing left to schedule"


def test_run_loop_requires_the_program_to_name_actions(
    config: HirakuConfig, task: Task
) -> None:
    _save_program(config, "empty-loop")
    with pytest.raises(AgentError):
        run_loop("empty-loop", "vo-backend", config=config)


def test_run_loop_rejects_two_actions_bound_to_the_same_workflow(
    config: HirakuConfig, task: Task
) -> None:
    _save_action(config, "a1", "vo-backend", workflow="design")
    _save_action(config, "a2", "vo-backend", workflow="design")
    _save_program(config, "ambiguous-loop", actions=["a1", "a2"])
    with pytest.raises(AgentError):
        run_loop("ambiguous-loop", "vo-backend", config=config)


def test_run_loop_rejects_an_action_with_no_recognized_workflow(
    config: HirakuConfig, task: Task
) -> None:
    _save_action(config, "a1", "vo-backend", workflow="implement")
    _save_program(config, "bad-loop", actions=["a1"])
    with pytest.raises(AgentError):
        run_loop("bad-loop", "vo-backend", config=config)


def test_run_loop_stops_immediately_on_max_experiments(
    config: HirakuConfig, task: Task
) -> None:
    create_node("baseline", task=task, kind="baseline", expectation="x", config=config)
    _save_action(config, "auto-design", "vo-backend", workflow="design")
    _save_program(config, "capped-loop", actions=["auto-design"], max_experiments=1)

    # No `agent_cmd` is even resolvable here (no real agent on PATH) — if the budget
    # check did not stop the loop before the first agent call, this would raise
    # instead of returning a stop reason.
    result = run_loop("capped-loop", "vo-backend", config=config)
    assert result.steps == []
    assert "max_experiments" in result.stopped


def test_run_loop_max_steps_overrides_everything(
    config: HirakuConfig, task: Task
) -> None:
    _save_action(config, "auto-design", "vo-backend", workflow="design")
    _save_program(config, "loop", actions=["auto-design"])

    result = run_loop("loop", "vo-backend", max_steps=0, config=config)
    assert result.steps == []
    assert result.stopped == "max_steps (0) reached"


def test_run_loop_stops_when_a_step_writes_nothing(
    config: HirakuConfig, task: Task, tmp_path: Path
) -> None:
    payload = {"experiments": [], "notes": "nothing worth proposing yet"}
    script = _stub_script(tmp_path, payload)
    _save_action(
        config,
        "auto-design",
        "vo-backend",
        workflow="design",
        agent_cmd=f"python3 {script}",
    )
    _save_program(config, "loop", actions=["auto-design"])

    result = run_loop("loop", "vo-backend", config=config)
    assert len(result.steps) == 1
    assert result.steps[0].written == []
    assert "wrote nothing" in result.stopped
