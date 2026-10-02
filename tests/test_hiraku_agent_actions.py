"""Tests for `hiraku_agent.actions`: translating a saved `Action`/`Context` record's
fields into the keyword overrides `workflows.py`'s `run_*` functions already take
(Phase 0 of `hiraku/program-action-merge-proposal-zou.md`).

No real LLM is called anywhere here — where a test needs `run_design` to actually run
an agent command, it points `agent_cmd` at a tiny on-disk Python script that prints a
fixed JSON reply, the same trick `test_hiraku_agent_workflows.py`'s `stub_agent()`
uses, just stored as a plain string (an `Action`'s `agent_cmd` field, exactly like
`--agent-cmd`, has to survive `shlex.split`) rather than passed as an argv list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.agent import Action, Context
from hiraku.config import HirakuConfig
from hiraku.record import Record
from hiraku_agent.actions import apply_overrides, resolve_action, resolve_context
from hiraku_agent.errors import AgentError
from hiraku_agent.workflows import run_design


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Record:
    return Record.create(
        "vo-backend",
        parent_id=None,
        type="task",
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


def _save_context(config: HirakuConfig, context_id: str, **fields: object) -> Context:
    context = Context(id=context_id, markdown="", **fields)
    context.save(config.contexts_dir / context_id / "context.md")
    return context


def _stub_script(tmp_path: Path, payload: dict) -> Path:
    """A tiny on-disk script that always prints `payload` as JSON, regardless of the
    args it's called with — safe to store as a plain `agent_cmd` string."""
    script = tmp_path / "stub_agent.py"
    script.write_text(f"print({json.dumps(json.dumps(payload))})\n")
    return script


# ------------------------------------------------------------------- resolve_context


def test_resolve_context_reads_the_paths_extra_field(
    config: HirakuConfig, tmp_path: Path
) -> None:
    (tmp_path / "notes.md").write_text("background")
    _save_context(config, "lit-review", paths=["notes.md"])
    assert resolve_context("lit-review", config=config) == ["notes.md"]


def test_resolve_context_with_no_paths_field_is_empty(config: HirakuConfig) -> None:
    _save_context(config, "empty-context")
    assert resolve_context("empty-context", config=config) == []


# -------------------------------------------------------------------- resolve_action


def test_resolve_action_reads_agent_cmd_timeout_and_literal_context(
    config: HirakuConfig,
) -> None:
    _save_action(
        config,
        "nightly-design",
        "vo-backend",
        workflow="design",
        agent_cmd="claude -p --dangerously-skip-permissions",
        timeout=1200,
        context=["notes.md"],
    )
    resolved = resolve_action("nightly-design", entity="vo-backend", config=config)
    assert resolved.command == ["claude", "-p", "--dangerously-skip-permissions"]
    assert resolved.timeout == 1200
    assert resolved.context_paths == ["notes.md"]


def test_resolve_action_context_can_name_a_context_record(
    config: HirakuConfig, tmp_path: Path
) -> None:
    (tmp_path / "lit.md").write_text("x")
    _save_context(config, "lit-review", paths=["lit.md"])
    _save_action(config, "a1", "vo-backend", workflow="design", context="lit-review")
    resolved = resolve_action("a1", entity="vo-backend", config=config)
    assert resolved.context_paths == ["lit.md"]


def test_resolve_action_with_no_extra_fields_resolves_to_nothing(
    config: HirakuConfig,
) -> None:
    _save_action(config, "bare", "vo-backend")
    resolved = resolve_action("bare", entity="vo-backend", config=config)
    assert resolved.command is None
    assert resolved.timeout is None
    assert resolved.context_paths == []


# -------------------------------------------------------------------- apply_overrides


def test_apply_overrides_lets_explicit_values_win(config: HirakuConfig) -> None:
    _save_action(
        config,
        "a1",
        "vo-backend",
        agent_cmd="claude -p",
        timeout=1200,
        context=["a.md"],
    )
    command, timeout, context_paths = apply_overrides(
        "a1",
        None,
        entity="vo-backend",
        command=["explicit"],
        timeout=30,
        context_paths=["b.md"],
        config=config,
    )
    assert command == ["explicit"]
    assert timeout == 30
    assert context_paths == ["b.md", "a.md"]


def test_apply_overrides_falls_back_to_the_action_when_nothing_explicit(
    config: HirakuConfig,
) -> None:
    _save_action(config, "a1", "vo-backend", agent_cmd="claude -p", timeout=1200)
    command, timeout, context_paths = apply_overrides(
        "a1",
        None,
        entity="vo-backend",
        command=None,
        timeout=None,
        context_paths=None,
        config=config,
    )
    assert command == ["claude", "-p"]
    assert timeout == 1200
    assert context_paths == []


def test_apply_overrides_with_no_action_or_context_is_a_no_op(
    config: HirakuConfig,
) -> None:
    command, timeout, context_paths = apply_overrides(
        None,
        None,
        entity="vo-backend",
        command=["x"],
        timeout=5,
        context_paths=["y.md"],
        config=config,
    )
    assert (command, timeout, context_paths) == (["x"], 5, ["y.md"])


def test_apply_overrides_needs_an_entity_when_action_is_given(
    config: HirakuConfig,
) -> None:
    with pytest.raises(AgentError):
        apply_overrides(
            "a1",
            None,
            entity=None,
            command=None,
            timeout=None,
            context_paths=None,
            config=config,
        )


def test_apply_overrides_context_id_adds_to_context_paths(
    config: HirakuConfig, tmp_path: Path
) -> None:
    (tmp_path / "lit.md").write_text("x")
    _save_context(config, "lit-review", paths=["lit.md"])
    _, _, context_paths = apply_overrides(
        None,
        "lit-review",
        entity=None,
        command=None,
        timeout=None,
        context_paths=["a.md"],
        config=config,
    )
    assert context_paths == ["a.md", "lit.md"]


# --------------------------------------------------------- integration with run_design


def test_run_design_uses_an_actions_agent_cmd_and_context(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    (tmp_path / "notes.md").write_text("Background: prior work found X.")
    payload = {
        "experiments": [
            {"id": "baseline", "kind": "baseline", "expectation": "x"},
        ],
        "notes": None,
    }
    script = _stub_script(tmp_path, payload)
    _save_action(
        config,
        "nightly-design",
        "vo-backend",
        workflow="design",
        agent_cmd=f"python3 {script}",
        context=["notes.md"],
    )
    # No `command=` given at all — if the Action's `agent_cmd` were not applied, this
    # would fall through to the real default (`claude -p`) and fail outright.
    result = run_design("vo-backend", action="nightly-design", config=config)
    assert len(result.written) == 1
    assert result.context.files.get("notes.md") == "Background: prior work found X."


def test_run_design_action_needs_a_known_task_id(config: HirakuConfig) -> None:
    # `run_design` always has a task id (it's the first positional argument), so this
    # exercises the same guard through `run_draft`, where the task id can genuinely be
    # unknown ahead of the call.
    from hiraku_agent.workflows import run_draft

    with pytest.raises(AgentError):
        run_draft("draft this", action="some-action", config=config)
