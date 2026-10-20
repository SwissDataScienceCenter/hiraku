from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.agent import main as agent_main
from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record
from hiraku.workflows.session import list_sessions


def invoke(runner: CliRunner, arguments: list[str], payload: Any = None) -> Any:
    result = runner.invoke(
        main, arguments, input=None if payload is None else json.dumps(payload)
    )
    assert result.exit_code == 0, result.output
    return result


def start(runner: CliRunner, workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = invoke(
        runner, ["workflow", "start", workflow, "--arguments-file", "-"], payload
    )
    response = json.loads(result.output)
    return response


@pytest.fixture
def runner(tmp_path: Path, monkeypatch) -> CliRunner:
    project = tmp_path / "workflow-examples"
    project.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True)
    monkeypatch.chdir(project)
    selected = CliRunner()
    invoke(selected, ["init", "--example", "workflow-examples"])
    workflows = project / "hiraku" / "workflows"
    assert (workflows / "count-steps" / "workflow.py").is_file()
    assert (workflows / "greet" / "workflow.md").is_file()
    assert (workflows / "scheduler" / "workflow.py").is_file()
    assert not (workflows / "greet" / "workflow.py").exists()
    assert not (project / "example.yml").exists()
    return selected


def send(
    runner: CliRunner, response: dict[str, Any], name: str, **arguments: Any
) -> dict[str, Any]:
    command = [
        "workflow",
        "send-event",
        response["session_id"],
        name,
        "--instruction-id",
        str(response["instruction_id"]),
        "--arguments-file",
        "-",
    ]
    result = json.loads(invoke(runner, command, arguments).output)
    assert "instruction" not in result
    if result["status"] == "accepted":
        shown = invoke(runner, ["session", "show", result["session_id"], "--json"])
        result.update(json.loads(shown.output))
        result["status"] = "accepted"
    return result


def test_count_steps_tokens_and_user_steps(runner: CliRunner) -> None:
    listed = invoke(runner, ["workflow", "list"]).output
    assert "count-steps" in listed
    assert "greet" in listed

    response = start(runner, "count-steps", {"count": 3})
    tokens = []
    for step in range(1, 4):
        instruction = response["instruction"]
        context = instruction["context"]
        assert instruction["text"].startswith("## Step")
        assert f"`Step {step}/3: token {context['token']}`" in instruction["text"]
        assert "{{" not in instruction["text"]
        options = instruction["events"]["accepted"]
        assert [item["event"] for item in options] == ["ack"]
        if tokens:
            stale = send(runner, response, "ack", token=tokens[-1])
            assert stale["status"] == "rejected"
            assert f"does not match step {step}" in stale["message"]
        tokens.append(context["token"])
        response = send(runner, response, "ack", token=context["token"])
        if step == 3:
            break
        gate = response["instruction"]
        assert gate["text"].startswith("## Next")
        assert "Send `step` only if the user agrees" in gate["text"]
        assert gate["context"] == {"done": step, "remaining": 3 - step}
        gate_options = gate["events"]["accepted"]
        assert [item["event"] for item in gate_options] == ["step", "finish"]
        response = send(runner, response, "step")

    assert response["session_status"] == "completed"
    assert response["instruction"]["text"].startswith("## Done")
    assert response["instruction"]["context"] == {"steps": 3, "count": 3}
    assert len(set(tokens)) == 3


def test_count_steps_rejects_wrong_tokens_and_finishes_early(
    runner: CliRunner,
) -> None:
    assert start(runner, "count-steps", {"count": 0})["status"] == "rejected"

    response = start(runner, "count-steps", {"count": 2})
    wrong = send(runner, response, "ack", token="00000000")
    assert wrong["status"] == "rejected"
    early = send(runner, response, "finish")
    assert early["status"] == "rejected"
    assert "not an event the current instruction accepts" in early["message"]
    gate = send(
        runner, response, "ack", token=response["instruction"]["context"]["token"]
    )
    done = send(runner, gate, "finish")
    assert done["session_status"] == "completed"
    assert done["instruction"]["context"] == {"steps": 1, "count": 2}


# A scripted harness: prints the step line and echoes the step's token.
ECHO_AGENT = """
import json, os, re
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
instruction = response["instruction"]
if response.get("session_status") == "completed":
    print("Ran", instruction["context"]["steps"], "steps")
    raise SystemExit
if "token" not in instruction["context"]:
    # Simulate ending the interactive conversation at the approval prompt.
    raise SystemExit
print(re.search(r"`(Step .*?)`", instruction["text"]).group(1))
event = {"event": instruction["events"]["accepted"][0]["event"],
         "arguments": {"token": instruction["context"]["token"]}}

Path(os.environ["HIRAKU_EVENT_FILE"]).write_text(json.dumps(event))

"""


def test_count_steps_can_pause_and_resume_interactive_conversations(
    runner: CliRunner, tmp_path: Path, monkeypatch, capfd
) -> None:
    script = tmp_path / "echo_agent.py"
    script.write_text(ECHO_AGENT)
    monkeypatch.setenv("HIRAKU_AGENT_INTERACTIVE_CMD", f"{sys.executable} {script}")

    started = runner.invoke(
        agent_main, ["count-steps", "--count", "3", "--interactive"]
    )
    output = capfd.readouterr().out
    assert started.exit_code == 3, started.output
    assert "Step 1/3: token" in output
    assert "Step 2/3" not in output
    assert "[1 Step] agent → ack  accepted" in started.output
    assert "No event was submitted; instruction 2 is still pending" in started.output

    second = runner.invoke(agent_main, ["count-steps", "step", "--interactive"])
    output = capfd.readouterr().out
    assert second.exit_code == 3, second.output
    assert "[2 Next] you → step  accepted" in second.output
    assert "Step 2/3: token" in output
    assert "Step 3/3" not in output

    (session,) = list_sessions(config=HirakuConfig.discover())
    # Step 2 is done; its token cannot be sent again while the session waits.
    old = session.history[-1].arguments["token"]
    stale = runner.invoke(agent_main, ["count-steps", "ack", "--token", old])
    assert stale.exit_code == 1
    assert "No active session accepts 'ack'" in stale.output

    done = runner.invoke(agent_main, ["count-steps", "step", "--interactive"])
    output = capfd.readouterr().out
    assert done.exit_code == 0, done.output
    assert "Step 3/3: token" in output
    assert "Ran 3 steps" in output
    assert 'completed: {"steps": 3, "count": 3}' in done.output


def test_greet_is_one_final_prompt_filled_from_its_arguments(
    runner: CliRunner,
) -> None:
    response = start(runner, "greet", {"name": "Ada", "times": 3})
    assert response["status"] == "accepted"
    assert response["session_status"] == "completed"
    instruction = response["instruction"]
    assert instruction["text"].startswith("# Greet\n\nPrint 3 greetings to Ada")
    assert "Greet formally:\nfalse." in instruction["text"]
    assert instruction["context"] == {"name": "Ada", "times": 3, "formal": False}
    assert "events" not in instruction

    default = start(runner, "greet", {})
    assert default["status"] == "accepted"
    assert default["instruction"]["context"] == {
        "name": "World",
        "times": 2,
        "formal": False,
    }


# A scripted harness for a final instruction: there is no event to send.
PROMPT_AGENT = """
import json, os
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
print(response["instruction"]["text"].splitlines()[2])
print("event file:", os.environ.get("HIRAKU_EVENT_FILE"))
"""


def test_greet_through_hiraku_agent(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "prompt_agent.py"
    script.write_text(PROMPT_AGENT)
    monkeypatch.setenv("HIRAKU_AGENT_CMD", f"{sys.executable} {script}")

    result = runner.invoke(agent_main, ["greet", "--name", "Ada", "--formal", "true"])
    assert result.exit_code == 0, result.output
    assert "[1 Greet] final" in result.output
    assert "Print 2 greetings to Ada, one per line. Greet formally:" in result.output
    assert "event file: None" in result.output
    assert 'completed: {"name": "Ada", "times": 2, "formal": true}' in result.output


def children(parent: str) -> list[Any]:
    config = HirakuConfig.discover()
    return sorted(
        load_record(parent, config=config).children(config=config),
        key=lambda item: item.id,
    )


def set_count(runner: CliRunner, record_id: str, count: int) -> None:
    invoke(runner, ["record", "update", record_id, "--set", f"count={count}"])


def test_scheduler_works_by_priority_until_every_record_reaches_the_target(
    runner: CliRunner,
) -> None:
    response = start(runner, "scheduler", {"records": 3, "target": 2})
    assert response["status"] == "accepted"
    parent = response["instruction"]["context"]["record"].rsplit("-", 1)[0]
    records = children(parent)
    assert len(records) == 3
    assert all(1 <= item.priority <= 9 and item.count == 0 for item in records)
    # The highest priority runs to the target first; ties go to the larger ID.
    order = sorted(records, key=lambda item: (item.priority, item.id), reverse=True)
    expected = [item.id for item in order for _ in range(2)]

    for done, record_id in enumerate(expected):
        instruction = response["instruction"]
        context = instruction["context"]
        assert context["record"] == record_id
        assert context["remaining"] == 6 - done
        count = context["count"]
        assert f"--set count={count + 1}" in instruction["text"]
        assert [item["event"] for item in instruction["events"]["accepted"]] == [
            "incremented"
        ]

        unchanged = send(runner, response, "incremented", record_id=record_id)
        assert unchanged["status"] == "rejected"
        assert f"set it to {count + 1}" in unchanged["message"]
        other = next(item.id for item in records if item.id != record_id)
        wrong = send(runner, response, "incremented", record_id=other)
        assert wrong["status"] == "rejected"
        assert "assigned record" in wrong["message"]

        set_count(runner, record_id, count + 1)
        response = send(runner, response, "incremented", record_id=record_id)
        assert response["status"] == "accepted"

    assert response["session_status"] == "completed"
    assert response["instruction"]["text"].startswith("## Done")
    summary = response["instruction"]["context"]["records"]
    assert [item["record"] for item in summary] == [item.id for item in records]
    assert {item["count"] for item in summary} == {2}


def test_scheduler_rejects_a_count_raised_too_far(runner: CliRunner) -> None:
    response = start(runner, "scheduler", {"records": 1, "target": 3})
    record_id = response["instruction"]["context"]["record"]
    set_count(runner, record_id, 2)
    skipped = send(runner, response, "incremented", record_id=record_id)
    assert skipped["status"] == "rejected"
    assert "is 2; set it to 1" in skipped["message"]
    set_count(runner, record_id, 1)
    accepted = send(runner, response, "incremented", record_id=record_id)
    assert accepted["instruction"]["context"]["count"] == 1


# A scripted harness: raises the assigned count with the CLI, then sends an event.
SCHEDULER_AGENT = """
import json, os, subprocess, sys
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
context = response["instruction"]["context"]
if response.get("session_status") == "completed":
    print("final:", [(item["record"], item["count"]) for item in context["records"]])
    raise SystemExit
command = ["record", "update", context["record"], "--set", f"count={context['next']}"]
subprocess.run([sys.executable, "-m", "hiraku.cli", *command], check=True)
event = {"event": "incremented", "arguments": {"record_id": context["record"]}}

Path(os.environ["HIRAKU_EVENT_FILE"]).write_text(json.dumps(event))

"""


def test_scheduler_runs_on_its_own_through_hiraku_agent(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "scheduler_agent.py"
    script.write_text(SCHEDULER_AGENT)
    monkeypatch.setenv("HIRAKU_AGENT_CMD", f"{sys.executable} {script}")

    result = runner.invoke(agent_main, ["scheduler", "--records", "2"])
    assert result.exit_code == 0, result.output
    # No user gate: four increments, then the final instruction.
    assert result.output.count("agent → incremented  accepted") == 4
    assert "[5 Done] final" in result.output
    assert "waiting for you" not in result.output
    (session,) = list_sessions(config=HirakuConfig.discover())
    assert session.status == "completed"


def test_example_workflows_draw_their_states(runner: CliRunner) -> None:
    count_steps = invoke(runner, ["workflow", "graph", "count-steps"]).output
    assert count_steps == (
        "stateDiagram-v2\n"
        "  [*] --> running\n"
        "  running --> waiting: ack\n"
        "  running --> finished: ack\n"
        "  waiting --> running: step\n"
        "  waiting --> finished: finish\n"
        "  finished --> [*]\n"
    )
    scheduler = invoke(runner, ["workflow", "graph", "scheduler"]).output
    assert "  working --> working: incremented\n" in scheduler
    assert "  working --> finished: incremented\n" in scheduler
