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
from hiraku.workflows import State, Instruction, Workflow, event, phase, start


class Answers(State):
    phase: str = "work"
    items: int
    answers: list[str] = Field(default_factory=list)


class Toy(Workflow):
    @start(dest="work")
    def start(
        self, *, items: Annotated[int, Field(gt=0, description="Items.")] = 2
    ) -> Answers:
        return Answers(items=items)

    @phase("work")
    def work(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Work"), context={"done": len(answers.answers)}
        )

    @phase("gate")
    def gate(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Gate"), context={"answers": answers.answers}
        )

    @phase("done", final=True)
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
                "phase": "done" if len(answers.answers) == answers.items else "gate"
            }
        )
        return answers

    @event(source="gate", dest="work")
    def approve(self, answers: Answers) -> Answers:
        """Go on with the next item."""
        return answers.model_copy(update={"phase": "work"})

    @event(source=["work", "gate"], dest="done")
    def stop(self, answers: Answers) -> Answers:
        """Stop early."""
        return answers.model_copy(update={"phase": "done"})


WORKFLOW = Toy
'''

# Answers events that take a value with the next line of FAKE_ANSWERS, and
# other events with the first accepted event; writes nothing when out of answers.
FAKE_AGENT = r"""import json, os, sys
from pathlib import Path
import anyio
from mcp import Client, StdioServerParameters

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
print("saw", response["instruction"]["text"].splitlines()[0])
if response["session_status"] == "completed":
    print("final, event file given:", "HIRAKU_EVENT_FILE" in os.environ)
    sys.exit(int(os.environ.get("FAKE_FINAL_EXIT", "0")))
if response["instruction"]["text"].startswith("## Gate") and os.environ.get(
    "FAKE_PAUSE_AT_GATE"
):
    sys.exit(0)


async def submit():
    settings = json.loads(Path(os.environ["HIRAKU_MCP_CONFIG"]).read_text())[
        "mcpServers"
    ]["hiraku"]
    async with Client(StdioServerParameters(**settings), mode="legacy") as client:
        tools = await client.list_tools()
        assert [t.name for t in tools.tools] == ["workflow_send_event"]
        assert not (await client.list_resources()).resources
        assert not (await client.list_prompts()).prompts
        for attempt in range(3):
            first = response["instruction"]["events"]["accepted"][0]
            arguments = {}
            if "value" in first["arguments"].get("properties", {}):
                answers = Path(os.environ["FAKE_ANSWERS"])
                lines = answers.read_text().splitlines() if answers.exists() else []
                if not lines:
                    return
                answers.write_text("".join(line + "\n" for line in lines[1:]))
                arguments = {"value": lines[0]}
            result = await client.call_tool(
                "workflow_send_event",
                {
                    "event": first["event"],
                    "arguments": arguments,
                },
            )
            value = json.loads(result.content[0].text)
            assert "instruction" not in value, value
            if value["status"] == "rejected":
                print(f"agent → {first['event']}  rejected: {value['message']}")
            else:
                break
        if os.environ.get("FAKE_DOUBLE_SUBMIT") and value["status"] == "accepted":
            duplicate = await client.call_tool(
                "workflow_send_event",
                {
                    "event": first["event"],
                    "arguments": arguments,
                },
            )
            assert json.loads(duplicate.content[0].text) == value
            other = await client.call_tool(
                "workflow_send_event",
                {
                    "event": "stop",
                    "arguments": {},
                },
            )
            assert json.loads(other.content[0].text)["status"] == "stale"


anyio.run(submit)
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
        {"session_id": s.id, "status": s.status, "state": s.phase}
        for s in list_sessions(config=config)
    ]


def test_runs_through_states_and_retries_rejected_events(
    config: HirakuConfig,
) -> None:
    answers(config, "bad", "one", "two")
    started = agent("toy", "--items", "2")
    assert started.exit_code == 0, started.output
    assert "session toy-" in started.output
    assert "saw ## Work" in started.output
    assert "agent → answer  rejected: bad is not an answer" in started.output
    assert "agent → answer  accepted" in started.output
    assert "agent → approve  accepted" in started.output

    (session,) = sessions(config)
    logs = config.sessions_dir / session["session_id"] / "1"
    entries = json.loads((logs / "1/submissions.json").read_text())
    assert [e["result"]["status"] for e in entries] == ["rejected", "accepted"]
    assert entries[1]["arguments"] == {"value": "one"}

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
    assert "saw" not in shown.output


def test_rejected_events_stop_after_three_attempts(config: HirakuConfig) -> None:
    answers(config, "bad", "bad", "bad", "one")
    result = agent("toy")
    assert result.exit_code == 1
    assert "bad is not an answer" in result.output
    assert (config.root / "answers.txt").read_text() == "one\n"
    (session,) = sessions(config)
    stored = load_session(session["session_id"], config=config)
    assert stored.status == "active"
    assert stored.instruction_id == 1
    assert stored.state["answers"] == []


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
    assert "mcp__hiraku__workflow_send_event" in DEFAULT_COMMANDS[True]
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


def test_worker_only_submits_one_assignment(config, monkeypatch):
    answers(config, "one")
    monkeypatch.setenv("FAKE_DOUBLE_SUBMIT", "1")
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
def test_subprocess_failure_is_propagated_after_saved_submission(
    config, monkeypatch, variable
):
    answers(config, "one")
    monkeypatch.setenv(variable, "7")
    result = agent("toy", "--items", "1")
    assert result.exit_code == 1, result.output
    assert "Agent exited with status 7" in result.output
    (session,) = list_sessions(config=config)
    assert session.status == "completed"
    assert session.state["answers"] == ["one"]
