from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from hiraku.agent import DEFAULT_COMMANDS, main
from hiraku.config import HirakuConfig
from hiraku.workflows.session import list_sessions, load_session

WORKFLOW_MD = """---
id: toy
description: Answer items with a conversational approval step.
---
# Toy

Answer each item.

## Work

Send the event `answer` with a value.

## Gate

Ask the user whether to go on.

## Done

Tell the user the answers.
"""

WORKFLOW_PY = r'''from __future__ import annotations
from typing import Annotated
from pydantic import Field
from hiraku.errors import EventRejected, NotReady
from hiraku.workflows import State, Instruction, Workflow, event, node, start


class Answers(State):
    node: str = "work"
    items: int
    answers: list[str] = Field(default_factory=list)


class Toy(Workflow):
    @start(dest="work")
    def start(
        self, *, items: Annotated[int, Field(gt=0, description="Items.")] = 2
    ) -> Answers:
        return Answers(items=items)

    @node("work")
    def work(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Work"), context={"done": len(answers.answers)}
        )

    @node("gate")
    def gate(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Gate"), context={"answers": answers.answers}
        )

    @node("done", final=True)
    def done(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"), context={"answers": answers.answers}
        )

    @event(source="work", dest=["gate", "done"])
    def answer(
        self, answers: Answers, *, value: Annotated[str, Field(description="Value.")]
    ) -> Answers:
        """Answer the current item."""
        if value == "bad":
            raise EventRejected("bad is not an answer")
        if value == "wait" and (not (self.config.root / "ready").exists()):
            raise NotReady("not ready yet")
        answers = answers.model_copy(update={"answers": [*answers.answers, value]})
        answers = answers.model_copy(
            update={
                "node": "done" if len(answers.answers) == answers.items else "gate"
            }
        )
        return answers

    @event(source="gate", dest="work")
    def approve(self, answers: Answers) -> Answers:
        """Go on with the next item."""
        return answers.model_copy(update={"node": "work"})

    @event(source=["work", "gate"], dest="done")
    def stop(self, answers: Answers) -> Answers:
        """Stop early."""
        return answers.model_copy(update={"node": "done"})


WORKFLOW = Toy
'''

# Answers events that take a value with the next line of FAKE_ANSWERS, and
# other events with the first accepted event; writes nothing when out of answers.
FAKE_AGENT = r"""import json, os, sys
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
print("saw", response["instruction"]["text"].splitlines()[0])
if response["session_status"] == "completed":
    print("final, event file given:", "HIRAKU_EVENT_FILE" in os.environ)
    sys.exit(int(os.environ.get("FAKE_FINAL_EXIT", "0")))
if response["instruction"]["text"].startswith("## Gate") and os.environ.get(
    "FAKE_PAUSE_AT_GATE"
):
    sys.exit(0)


assert "HIRAKU_MCP_CONFIG" not in os.environ
event_file = Path(os.environ["HIRAKU_EVENT_FILE"])
if "FAKE_EVENT_JSON" in os.environ:
    event_file.write_text(os.environ["FAKE_EVENT_JSON"])
else:
    first = response["instruction"]["events"]["accepted"][0]
    arguments = {}
    if "value" in first["arguments"].get("properties", {}):
        answers = Path(os.environ["FAKE_ANSWERS"])
        lines = answers.read_text().splitlines() if answers.exists() else []
        if not lines:
            sys.exit(0)
        answers.write_text("".join(line + "\n" for line in lines[1:]))
        arguments = {"value": lines[0]}
    event_file.write_text(json.dumps({"event": first["event"], "arguments": arguments}))
sys.exit(int(os.environ.get("FAKE_WORKER_EXIT", "0")))
"""


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    directory = selected.workflows_dir / "toy"
    directory.mkdir(parents=True)
    (directory / "workflow.md").write_text(WORKFLOW_MD)
    (directory / "workflow.py").write_text(WORKFLOW_PY)
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(agent))}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HIRAKU_AGENT_CMD", command)
    monkeypatch.setenv("HIRAKU_AGENT_INTERACTIVE_CMD", command)
    monkeypatch.setenv("FAKE_ANSWERS", str(tmp_path / "answers.txt"))
    return selected


def answers(config: HirakuConfig, *values: str) -> None:
    (config.root / "answers.txt").write_text("".join(f"{v}\n" for v in values))


def agent(*arguments: str) -> Result:
    return CliRunner().invoke(main, list(arguments))


def sessions(config: HirakuConfig) -> list[dict]:
    return [
        {"session_id": s.id, "status": s.status, "state": s.node}
        for s in list_sessions(config=config)
    ]


def test_runs_through_states_and_submits_event_files(
    config: HirakuConfig,
) -> None:
    answers(config, "one", "two")
    started = agent("toy", "--items", "2")
    assert started.exit_code == 0, started.output
    assert "session toy-" in started.output
    assert "saw ## Work" in started.output
    assert "agent → answer  accepted" in started.output
    assert "agent → approve  accepted" in started.output

    (session,) = sessions(config)
    logs = config.sessions_dir / session["session_id"] / "1"
    assert json.loads((logs / "1/event.json").read_text()) == {
        "event": "answer",
        "arguments": {"value": "one"},
    }
    assert not list(logs.rglob("mcp.json"))
    assert not list(logs.rglob("submissions.json"))

    # The agent carries out the final instruction, which takes no event.
    assert "[4 Done] final" in started.output
    assert "saw ## Done" in started.output
    assert "final, event file given: False" in started.output
    assert 'completed: {"answers": ["one", "two"]}' in started.output
    final = config.sessions_dir / session["session_id"] / "4/1/prompt.md"
    assert "final instruction. Carry it out" in final.read_text()

    # A completed session named with --session is shown, not carried out again.
    shown = agent("toy", "--session", session["session_id"])
    assert shown.exit_code == 0, shown.output
    assert "saw" not in shown.output


def test_rejected_event_stops_and_can_be_retried_explicitly(
    config: HirakuConfig,
) -> None:
    answers(config, "bad", "one", "two")
    result = agent("toy")
    assert result.exit_code == 1
    assert "bad is not an answer" in result.output
    assert (config.root / "answers.txt").read_text() == "one\ntwo\n"
    (session,) = sessions(config)
    stored = load_session(session["session_id"], config=config)
    assert stored.status == "active"
    assert stored.instruction_id == 1
    assert stored.state["answers"] == []
    event_file = config.sessions_dir / stored.id / "1/1/event.json"
    assert json.loads(event_file.read_text())["arguments"] == {"value": "bad"}
    assert not (event_file.parents[1] / "pending.json").exists()
    resumed = agent("toy")
    assert resumed.exit_code == 0, resumed.output
    assert load_session(stored.id, config=config).state["answers"] == ["one", "two"]


@pytest.mark.parametrize("command", [("toy",), ("toy", "approve")])
def test_retries_option_is_not_supported(
    config: HirakuConfig, command: tuple[str, ...]
) -> None:
    result = agent(*command, "--retries", "0")
    assert result.exit_code == 2
    assert "No such option '--retries'" in result.output
    assert sessions(config) == []


def test_not_ready_events_are_sent_again_without_the_agent(
    config: HirakuConfig,
) -> None:
    answers(config, "wait")
    waiting = agent("toy", "--items", "1")
    assert waiting.exit_code == 4, waiting.output
    assert "not_ready: not ready yet" in waiting.output
    assert "Waiting for evidence" in waiting.output

    still = agent("toy")
    assert still.exit_code == 4, still.output
    assert "Waiting for evidence" in still.output
    assert "saw" not in still.output

    (config.root / "ready").write_text("")
    ready = agent("toy")
    assert ready.exit_code == 0, ready.output
    assert "completed" in ready.output
    assert "saw ## Work" not in ready.output
    assert "saw ## Done" in ready.output


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "{}",
        '{"event": "answer", "arguments": []}',
        '{"event": "answer", "arguments": {}, "session_id": "other"}',
    ],
)
def test_invalid_event_file_stops_without_transition(config, monkeypatch, content):
    monkeypatch.setenv("FAKE_EVENT_JSON", content)
    result = agent("toy")
    assert result.exit_code == 1, result.output
    assert "Invalid event file" in result.output
    (session,) = list_sessions(config=config)
    assert session.instruction_id == 1
    assert session.state["answers"] == []
    assert (config.sessions_dir / session.id / "1/1/event.json").read_text() == content


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "missing"},
        {"event": "answer", "arguments": {}},
    ],
)
def test_invalid_event_is_rejected_by_workflow(config, monkeypatch, payload):
    monkeypatch.setenv("FAKE_EVENT_JSON", json.dumps(payload))
    result = agent("toy")
    assert result.exit_code == 1, result.output
    assert "rejected" in result.output
    assert "Invalid event file" not in result.output
    (session,) = list_sessions(config=config)
    assert session.instruction_id == 1
    assert session.state["answers"] == []


def test_rejected_pending_submission_stops_without_relaunch(config):
    answers(config, "wait")
    assert agent("toy", "--items", "1").exit_code == 4
    path = config.workflows_dir / "toy/workflow.py"
    path.write_text(WORKFLOW_PY.replace('value == "bad"', 'value in ("bad", "wait")'))
    resumed = agent("toy")
    assert resumed.exit_code == 1, resumed.output
    assert "rejected: bad is not an answer" in resumed.output
    assert "saw" not in resumed.output
    (session,) = list_sessions(config=config)
    assert not (config.sessions_dir / session.id / "1/pending.json").exists()
    assert (config.sessions_dir / session.id / "1/1/event.json").exists()


def test_worker_environment_does_not_leak_inherited_submission_paths(
    config, monkeypatch
):
    monkeypatch.setenv("HIRAKU_MCP_CONFIG", "old-mcp-config.json")
    monkeypatch.setenv("HIRAKU_EVENT_FILE", "old-event.json")
    answers(config, "one")
    result = agent("toy", "--items", "1")
    assert result.exit_code == 0, result.output
    assert "final, event file given: False" in result.output
    assert not (config.root / "old-event.json").exists()


def test_sessions_are_selected_by_workflow_and_pending_event(
    config: HirakuConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PAUSE_AT_GATE", "1")
    answers(config, "one")
    assert agent("toy", "--interactive").exit_code == 3
    again = agent("toy", "--items", "3")
    assert again.exit_code == 1
    assert "already has an active session" in again.output

    # Out of answers: the agent writes no event and the session stays at Work.
    second = agent("toy", "--new")
    assert second.exit_code == 1
    assert "Agent submitted no event" in second.output
    assert len(sessions(config)) == 2

    several = agent("toy")
    assert several.exit_code == 1
    assert "Several toy sessions are active" in several.output

    # Only the first session is at the gate, so approve selects it.
    answers(config, "two")
    approved = agent("toy", "approve")
    assert approved.exit_code == 0, approved.output
    assert "completed" in approved.output

    nobody = agent("toy", "approve")
    assert nobody.exit_code == 1
    assert "No active session accepts 'approve'" in nobody.output

    (remaining,) = [item for item in sessions(config) if item["status"] == "active"]
    stopped = agent("toy", "stop", "--session", remaining["session_id"])
    assert stopped.exit_code == 0, stopped.output
    assert agent("toy", "approve").output.startswith("Error: No active toy session")


def test_interactive_mode_pauses_when_the_agent_writes_no_event(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PAUSE_AT_GATE", "1")
    answers(config, "one", "two")
    first = agent("toy", "--interactive")
    assert first.exit_code == 3, first.output
    assert "agent → answer  accepted" in first.output
    assert "No event was submitted; instruction 2 is still pending" in first.output
    (session,) = sessions(config)
    assert session["state"] == "gate"
    step = config.sessions_dir / session["session_id"] / "1/1/prompt.md"
    assert "wait for their go-ahead" in step.read_text()

    second = agent("toy", "approve", "--interactive")
    assert second.exit_code == 0, second.output
    assert "you → approve  accepted" in second.output
    assert "completed" in second.output

    answers(config)
    paused = agent("toy", "--interactive", "--new")
    assert paused.exit_code == 3, paused.output
    assert "No event was submitted; instruction 1 is still pending" in paused.output


def test_arguments_are_validated_by_the_workflow(config: HirakuConfig) -> None:
    help_text = agent("toy", "--help").output
    assert "--items INTEGER" not in help_text
    assert "--items JSON" in help_text
    assert "approve" in help_text and "answer" in help_text
    event_help = " ".join(agent("toy", "answer", "--help").output.split())
    assert "--value TEXT Value. [required]" in event_help

    invalid = agent("toy", "--items", "0")
    assert invalid.exit_code == 1
    assert "items: Input should be greater than 0" in invalid.output
    assert sessions(config) == []

    answers(config)
    agent("toy")
    empty = agent("toy", "answer")
    assert empty.exit_code == 1
    assert "value: Field required" in empty.output


def test_default_interactive_command_ends_its_options() -> None:
    # Otherwise the variadic --allowedTools takes the opening message as a tool.
    assert "mcp" not in DEFAULT_COMMANDS[True]
    assert "--" not in shlex.split(DEFAULT_COMMANDS[False])


def test_missing_agent_command_is_explained(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_AGENT_CMD", "no-such-agent-command -p")
    result = agent("toy")
    assert result.exit_code == 1
    assert "Cannot run the agent command 'no-such-agent-command'" in result.output


def test_event_subcommands_use_method_names(
    config: HirakuConfig,
) -> None:
    result = agent("collaborative", "revise_implementation", "--help")
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "Rework the implementation with the user's feedback." in text
    assert "--feedback TEXT What the user wants changed. [required]" in text
    removed = agent("collaborative", "revise", "--help")
    assert removed.exit_code == 2
    assert "No such command 'revise'" in removed.output


def test_worker_file_is_bound_to_the_dispatched_instruction(config):
    answers(config, "one")
    result = agent("toy", "--items", "1")
    assert result.exit_code == 0, result.output
    (session,) = list_sessions(config=config)
    assert len(session.history) == 2
    assert session.state["answers"] == ["one"]


def test_markdown_only_cli_inputs_and_final_assignment(config):
    directory = config.workflows_dir / "markdown"
    directory.mkdir()
    (directory / "workflow.md").write_text(
        "---\nid: markdown\ndescription: A standalone prompt.\n"
        "arguments:\n  count: 5\n  enabled: true\n"
        "---\nCount {{count}}; enabled={{enabled}}."
    )
    help_text = agent("markdown", "--help")
    assert help_text.exit_code == 0
    assert "A standalone prompt." in help_text.output
    assert "--count JSON" in help_text.output
    assert "--enabled JSON" in help_text.output
    result = agent("markdown", "--count", "2", "--enabled", "false")
    assert result.exit_code == 0, result.output
    assert "Count 2; enabled=false." in result.output
    assert 'completed: {"count": 2, "enabled": false}' in result.output
    assert not (directory / "workflow.py").exists()


@pytest.mark.parametrize("variable", ["FAKE_WORKER_EXIT", "FAKE_FINAL_EXIT"])
def test_subprocess_failure_does_not_submit_worker_output(
    config, monkeypatch, variable
):
    answers(config, "one")
    monkeypatch.setenv(variable, "7")
    result = agent("toy", "--items", "1")
    assert result.exit_code == 1, result.output
    assert "Agent exited with status 7" in result.output
    (session,) = list_sessions(config=config)
    if variable == "FAKE_WORKER_EXIT":
        assert session.status == "active"
        assert session.state["answers"] == []
        assert (config.sessions_dir / session.id / "1/1/event.json").exists()
        monkeypatch.delenv("FAKE_WORKER_EXIT")
        answers(config, "two")
        resumed = agent("toy")
        assert resumed.exit_code == 0, resumed.output
        assert load_session(session.id, config=config).state["answers"] == ["two"]
        assert (config.sessions_dir / session.id / "1/2/event.json").exists()
    else:
        assert session.status == "completed"
        assert session.state["answers"] == ["one"]
