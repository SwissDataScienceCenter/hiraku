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

Reply `answer` with a value.

## Gate

Ask the user whether to go on.
"""

WORKFLOW_PY = '''
from pathlib import Path
from typing import Annotated

from pydantic import Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply


class ToySession(Session):
    items: int
    answers: list[str] = Field(default_factory=list)


class Toy(Workflow):
    def start(
        self, items: Annotated[int, Field(gt=0, description="Items.")] = 2
    ) -> Instruction:
        return self._work(ToySession(items=items))

    @reply
    def answer(
        self, session: ToySession, value: Annotated[str, Field(description="Value.")]
    ) -> Instruction | Completion:
        """Answer the current item."""
        if value == "bad":
            raise SubmissionRejected("bad is not an answer")
        if value == "wait" and not (self.config.root / "ready").exists():
            raise NotReady("not ready yet")
        session.answers.append(value)
        if len(session.answers) == session.items:
            return self.completion(session, {"answers": session.answers})
        return self.instruction(
            session,
            self.section("gate"),
            context={"answers": session.answers},
            requires_user_response=True,
            replies=[self.approve, self.stop],
        )

    @reply
    def approve(self, session: ToySession) -> Instruction:
        """Go on with the next item."""
        return self._work(session)

    @reply
    def stop(self, session: ToySession) -> Completion:
        """Stop early."""
        return self.completion(session, {"answers": session.answers})

    def _work(self, session: ToySession) -> Instruction:
        return self.instruction(
            session,
            self.section("work"),
            context={"done": len(session.answers)},
            replies=[self.answer, self.stop],
        )


WORKFLOW = Toy
'''

# Answers replies that take a value with the next line of FAKE_ANSWERS, and
# other replies with the first listed reply; writes nothing when out of answers.
FAKE_AGENT = """
import json, os, sys
from pathlib import Path

prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
reply_file = os.environ["HIRAKU_REPLY_FILE"]
print("saw", response["instruction"]["text"].splitlines()[0])
print("prompt names reply file:", reply_file in prompt)
first = response["instruction"]["replies"][0]
arguments = {}
if "value" in first["arguments"].get("properties", {}):
    answers = Path(os.environ["FAKE_ANSWERS"])
    lines = answers.read_text().splitlines() if answers.exists() else []
    if not lines:
        sys.exit(0)
    answers.write_text("".join(line + "\\n" for line in lines[1:]))
    arguments = {"value": lines[0]}
reply = {"reply": first["reply"], "arguments": arguments}
Path(reply_file).write_text(json.dumps(reply))
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


def test_runs_until_a_gate_and_continues_after_the_user_reply(
    config: HirakuConfig,
) -> None:
    answers(config, "bad", "one", "two")
    started = agent("toy", "--items", "2")
    assert started.exit_code == 3, started.output
    assert "session toy-" in started.output
    assert "saw ## Work" in started.output
    assert "prompt names reply file: True" in started.output
    assert "agent → answer  rejected: bad is not an answer" in started.output
    assert "agent → answer  accepted" in started.output
    assert "waiting for you" in started.output
    assert "hiraku-agent toy approve  # Go on with the next item." in started.output

    (session,) = sessions(config)
    logs = config.sessions_dir / session["session_id"] / "1"
    assert "Your previous reply was rejected: bad" in (logs / "2/prompt.md").read_text()
    assert json.loads((logs / "2/reply.json").read_text())["arguments"] == {
        "value": "one"
    }

    approved = agent("toy", "approve")
    assert approved.exit_code == 0, approved.output
    assert "you → approve  accepted" in approved.output
    assert 'completed: {"answers": ["one", "two"]}' in approved.output


def test_not_ready_replies_are_sent_again_without_the_agent(
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


def test_sessions_are_selected_by_workflow_and_pending_reply(
    config: HirakuConfig,
) -> None:
    answers(config, "one")
    assert agent("toy").exit_code == 3
    again = agent("toy", "--items", "3")
    assert again.exit_code == 1
    assert "already has an active session" in again.output

    # Out of answers: the agent writes no reply and the session stays at Work.
    second = agent("toy", "--new")
    assert second.exit_code == 1
    assert "The agent wrote no reply" in second.output
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
    assert "No reply was written; instruction 1 is still pending" in paused.output


def test_arguments_are_validated_by_the_workflow(config: HirakuConfig) -> None:
    help_text = agent("toy", "--help").output
    assert "--items INTEGER" not in help_text
    assert "--items JSON" in help_text
    assert "approve" in help_text and "answer" in help_text
    reply_help = " ".join(agent("toy", "answer", "--help").output.split())
    assert "--value TEXT Value. [required]" in reply_help

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
