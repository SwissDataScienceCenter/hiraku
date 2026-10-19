from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from hiraku.agent import DEFAULT_COMMANDS, main
from hiraku.config import HirakuConfig
from hiraku.session import Session

WORKFLOW_MD = """---
id: toy
description: Answer items, with a user gate after each one.
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

WORKFLOW_PY = '''
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from hiraku.errors import EventRejected, NotReady
from hiraku.workflow import Instruction, Workflow, event, state


class Answers(BaseModel):
    items: int
    answers: list[str] = Field(default_factory=list)


class Toy(Workflow):
    def start(
        self, items: Annotated[int, Field(gt=0, description="Items.")] = 2
    ) -> Answers:
        return Answers(items=items)

    @state("work", start=True)
    def work(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Work"), context={"done": len(answers.answers)}
        )

    @state("gate")
    def gate(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Gate"),
            context={"answers": answers.answers},
            requires_user_response=True,
        )

    @state("done", final=True)
    def done(self, answers: Answers) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"), context={"answers": answers.answers}
        )

    @event(source="work", dest=["gate", "done"])
    def answer(
        self, answers: Answers, value: Annotated[str, Field(description="Value.")]
    ) -> str:
        """Answer the current item."""
        if value == "bad":
            raise EventRejected("bad is not an answer")
        if value == "wait" and not (self.config.root / "ready").exists():
            raise NotReady("not ready yet")
        answers.answers.append(value)
        return "done" if len(answers.answers) == answers.items else "gate"

    @event(source="gate", dest="work")
    def approve(self, answers: Answers) -> None:
        """Go on with the next item."""

    @event(source=["work", "gate"], dest="done")
    def stop(self, answers: Answers) -> None:
        """Stop early."""


WORKFLOW = Toy
'''

# Answers events that take a value with the next line of FAKE_ANSWERS, and
# other events with the first accepted event; writes nothing when out of answers.
FAKE_AGENT = """
import json, os, sys
from pathlib import Path

prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
print("saw", response["instruction"]["text"].splitlines()[0])
if response.get("completed"):
    print("final, event file given:", "HIRAKU_EVENT_FILE" in os.environ)
    sys.exit(0)
event_file = os.environ["HIRAKU_EVENT_FILE"]
print("prompt names event file:", event_file in prompt)
first = response["instruction"]["events"]["accepted"][0]
arguments = {}
if "value" in first["arguments"].get("properties", {}):
    answers = Path(os.environ["FAKE_ANSWERS"])
    lines = answers.read_text().splitlines() if answers.exists() else []
    if not lines:
        sys.exit(0)
    answers.write_text("".join(line + "\\n" for line in lines[1:]))
    arguments = {"value": lines[0]}
event = {"event": first["event"], "arguments": arguments}
Path(event_file).write_text(json.dumps(event))
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
    return Session.list_all(config=config)


def test_runs_until_a_gate_and_continues_after_the_user_event(
    config: HirakuConfig,
) -> None:
    answers(config, "bad", "one", "two")
    started = agent("toy", "--items", "2")
    assert started.exit_code == 3, started.output
    assert "session toy-" in started.output
    assert "saw ## Work" in started.output
    assert "prompt names event file: True" in started.output
    assert "agent → answer  rejected: bad is not an answer" in started.output
    assert "agent → answer  accepted" in started.output
    assert "waiting for you" in started.output
    assert "hiraku-agent toy approve  # Go on with the next item." in started.output

    (session,) = sessions(config)
    logs = config.sessions_dir / session["session_id"] / "1"
    assert "Your previous event was rejected: bad" in (logs / "2/prompt.md").read_text()
    assert json.loads((logs / "2/event.json").read_text())["arguments"] == {
        "value": "one"
    }

    approved = agent("toy", "approve")
    assert approved.exit_code == 0, approved.output
    assert "you → approve  accepted" in approved.output
    # The agent carries out the final instruction, which takes no event.
    assert "[4 Done] final" in approved.output
    assert "saw ## Done" in approved.output
    assert "final, event file given: False" in approved.output
    assert 'completed: {"answers": ["one", "two"]}' in approved.output
    final = config.sessions_dir / session["session_id"] / "4/1/prompt.md"
    assert "final instruction of the session; it takes no event" in final.read_text()

    # A completed session named with --session is shown, not carried out again.
    shown = agent("toy", "--session", session["session_id"])
    assert shown.exit_code == 0, shown.output
    assert "saw" not in shown.output
    assert 'completed: {"answers": ["one", "two"]}' in shown.output


def test_not_ready_events_are_sent_again_without_the_agent(
    config: HirakuConfig,
) -> None:
    answers(config, "wait")
    waiting = agent("toy")
    assert waiting.exit_code == 4, waiting.output
    assert "not_ready: not ready yet" in waiting.output
    assert "Waiting for evidence" in waiting.output

    still = agent("toy")
    assert still.exit_code == 4, still.output
    assert "again → answer  not_ready" in still.output
    assert "saw" not in still.output

    (config.root / "ready").write_text("")
    ready = agent("toy")
    assert ready.exit_code == 3, ready.output
    assert "again → answer  accepted" in ready.output
    assert "saw" not in ready.output


def test_sessions_are_selected_by_workflow_and_pending_event(
    config: HirakuConfig,
) -> None:
    answers(config, "one")
    assert agent("toy").exit_code == 3
    again = agent("toy", "--items", "3")
    assert again.exit_code == 1
    assert "already has an active session" in again.output

    # Out of answers: the agent writes no event and the session stays at Work.
    second = agent("toy", "--new")
    assert second.exit_code == 1
    assert "The agent wrote no event" in second.output
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


def test_interactive_mode_stops_at_user_gates(config: HirakuConfig) -> None:
    answers(config, "one", "two")
    first = agent("toy", "--interactive")
    assert first.exit_code == 3, first.output
    assert "agent → answer  accepted" in first.output
    assert "waiting for you" in first.output
    (session,) = sessions(config)
    step = config.sessions_dir / session["session_id"] / "1/1/prompt.md"
    assert "until the user tells you to go ahead" in step.read_text()

    second = agent("toy", "approve", "--interactive")
    assert second.exit_code == 0, second.output
    assert "you → approve  accepted" in second.output
    assert "completed" in second.output

    answers(config)
    paused = agent("toy", "--interactive", "--new")
    assert paused.exit_code == 3, paused.output
    assert "No event was written; instruction 1 is still pending" in paused.output


def test_arguments_are_validated_by_the_workflow(config: HirakuConfig) -> None:
    help_text = agent("toy", "--help").output
    assert "--items INTEGER" not in help_text
    assert "--items JSON" in help_text
    assert "approve" in help_text and "answer" in help_text
    event_help = " ".join(agent("toy", "answer", "--help").output.split())
    assert "--value TEXT Value. [required]" in event_help

    invalid = agent("toy", "--items", "0")
    assert invalid.exit_code == 1
    assert "toy did not start (rejected): items" in invalid.output
    assert sessions(config) == []

    answers(config)
    agent("toy")
    empty = agent("toy", "answer")
    assert empty.exit_code == 1
    assert "value: Field required" in empty.output


def test_default_interactive_command_ends_its_options() -> None:
    # Otherwise the variadic --allowedTools takes the opening message as a tool.
    assert shlex.split(DEFAULT_COMMANDS[True])[-1] == "--"
    assert "--" not in shlex.split(DEFAULT_COMMANDS[False])


def test_missing_agent_command_is_explained(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIRAKU_AGENT_CMD", "no-such-agent-command -p")
    result = agent("toy")
    assert result.exit_code == 1
    assert "Cannot run the agent command 'no-such-agent-command'" in result.output
    assert "HIRAKU_AGENT_CMD" in result.output


def test_events_shared_by_several_states_are_one_subcommand(
    config: HirakuConfig,
) -> None:
    result = agent("collaborative", "revise", "--help")
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "In draft: Redraft the experiment with the user's feedback." in text
    assert "In analyze: Redo the analysis with the user's feedback." in text
    assert "--feedback TEXT What the user wants changed. [required]" in text


def test_events_requiring_approval_ask_in_the_terminal(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = config.workflows_dir / "toy" / "workflow.py"
    source.write_text(
        WORKFLOW_PY.replace(
            '@event(source="work", dest=["gate", "done"])',
            '@event(source="work", dest=["gate", "done"], requires_approval=True)',
        )
    )
    answers(config, "one", "uno")
    # Without a terminal to ask in, the event waits for the user's approval.
    waiting = agent("toy", "--items", "1")
    assert waiting.exit_code == 3, waiting.output
    assert "agent → answer  approval_required" in waiting.output
    assert "Waiting for your approval" in waiting.output

    monkeypatch.setattr("hiraku.agent._can_ask", lambda: True)
    asked = CliRunner().invoke(main, ["toy"], input="revise\nShorter\napprove\n")
    assert asked.exit_code == 0, asked.output
    assert "again → answer  approval_required" in asked.output
    assert "Approve event 'answer'?" in asked.output
    assert '"value": "one"' in asked.output
    assert "you → answer  accepted" in asked.output
    assert 'completed: {"answers": ["uno"]}' in asked.output
    (session,) = sessions(config)
    prompt = config.sessions_dir / session["session_id"] / "1/1/prompt.md"
    assert "The user asked for changes: Shorter" in prompt.read_text()
