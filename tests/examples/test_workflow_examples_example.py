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
from hiraku.session import Session


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
    return json.loads(result.output)


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


def reply(
    runner: CliRunner, response: dict[str, Any], name: str, **arguments: Any
) -> dict[str, Any]:
    command = [
        "workflow",
        "reply",
        response["session_id"],
        name,
        "--instruction-id",
        str(response["instruction_id"]),
        "--arguments-file",
        "-",
    ]
    return json.loads(invoke(runner, command, arguments).output)


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
        options = instruction["reply"]["options"]
        assert [item["reply"] for item in options] == ["ack"]
        if tokens:
            stale = reply(runner, response, "ack", token=tokens[-1])
            assert stale["status"] == "rejected"
            assert f"does not match step {step}" in stale["message"]
        tokens.append(context["token"])
        response = reply(runner, response, "ack", token=context["token"])
        if step == 3:
            break
        gate = response["instruction"]
        assert gate["text"].startswith("## Next")
        assert gate["reply"]["requires_user_response"] is True
        assert gate["context"] == {"done": step, "remaining": 3 - step}
        gate_options = gate["reply"]["options"]
        assert [item["reply"] for item in gate_options] == ["step", "finish"]
        response = reply(runner, response, "step")

    assert response["completed"] is True
    assert response["instruction"]["text"].startswith("## Done")
    assert response["instruction"]["context"] == {"steps": 3, "count": 3}
    assert len(set(tokens)) == 3


def test_count_steps_rejects_wrong_tokens_and_finishes_early(
    runner: CliRunner,
) -> None:
    assert start(runner, "count-steps", {"count": 0})["status"] == "rejected"

    response = start(runner, "count-steps", {"count": 2})
    wrong = reply(runner, response, "ack", token="00000000")
    assert wrong["status"] == "rejected"
    early = reply(runner, response, "finish")
    assert early["status"] == "rejected"
    assert "not a reply to the pending instruction" in early["message"]
    gate = reply(
        runner, response, "ack", token=response["instruction"]["context"]["token"]
    )
    done = reply(runner, gate, "finish")
    assert done["completed"] is True
    assert done["instruction"]["context"] == {"steps": 1, "count": 2}


# A scripted harness: prints the step line and echoes the step's token.
ECHO_AGENT = """
import json, os, re
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
instruction = response["instruction"]
if response.get("completed"):
    print("Ran", instruction["context"]["steps"], "steps")
    raise SystemExit
print(re.search(r"`(Step .*?)`", instruction["text"]).group(1))
reply = {"reply": instruction["reply"]["options"][0]["reply"],
         "arguments": {"token": instruction["context"]["token"]}}
Path(os.environ["HIRAKU_REPLY_FILE"]).write_text(json.dumps(reply))
"""


def test_count_steps_one_step_at_a_time_through_hiraku_agent(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "echo_agent.py"
    script.write_text(ECHO_AGENT)
    monkeypatch.setenv("HIRAKU_AGENT_CMD", f"{sys.executable} {script}")

    started = runner.invoke(agent_main, ["count-steps", "--count", "3"])
    assert started.exit_code == 3, started.output
    assert "Step 1/3: token" in started.output
    assert "Step 2/3" not in started.output
    assert "[1 Step] agent → ack  accepted" in started.output
    assert "[2 Next] waiting for you" in started.output
    assert "hiraku-agent count-steps step  # Run the next step." in started.output

    second = runner.invoke(agent_main, ["count-steps", "step"])
    assert second.exit_code == 3, second.output
    assert "[2 Next] you → step  accepted" in second.output
    assert "Step 2/3: token" in second.output
    assert "Step 3/3" not in second.output

    (summary,) = Session.list_all(config=HirakuConfig.discover())
    session = Session.load(summary["session_id"], config=HirakuConfig.discover())
    # Step 2 is done; its token cannot be sent again while the session waits.
    old = session.history[-1].arguments["token"]
    stale = runner.invoke(agent_main, ["count-steps", "ack", "--token", old])
    assert stale.exit_code == 1
    assert "No active session accepts 'ack'" in stale.output

    done = runner.invoke(agent_main, ["count-steps", "step"])
    assert done.exit_code == 0, done.output
    assert "Step 3/3: token" in done.output
    assert "Ran 3 steps" in done.output
    assert 'completed: {"steps": 3, "count": 3}' in done.output


def test_greet_is_one_final_prompt_filled_from_its_arguments(
    runner: CliRunner,
) -> None:
    response = start(runner, "greet", {"name": "Ada", "times": 3})
    assert response["status"] == "accepted"
    assert response["completed"] is True
    instruction = response["instruction"]
    assert instruction["text"].startswith("# Greet\n\nPrint 3 greetings to Ada")
    assert "Greet formally:\nfalse." in instruction["text"]
    assert instruction["context"] == {"name": "Ada", "times": 3, "formal": False}
    assert "reply" not in instruction

    missing = start(runner, "greet", {})
    assert missing["status"] == "rejected"
    assert "name" in missing["message"]


# A scripted harness for a final instruction: there is nothing to reply.
PROMPT_AGENT = """
import json, os
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
print(response["instruction"]["text"].splitlines()[2])
print("reply file:", os.environ.get("HIRAKU_REPLY_FILE"))
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
    assert "reply file: None" in result.output
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
        assert [item["reply"] for item in instruction["reply"]["options"]] == [
            "incremented"
        ]

        unchanged = reply(runner, response, "incremented", record_id=record_id)
        assert unchanged["status"] == "rejected"
        assert f"set it to {count + 1}" in unchanged["message"]
        other = next(item.id for item in records if item.id != record_id)
        wrong = reply(runner, response, "incremented", record_id=other)
        assert wrong["status"] == "rejected"
        assert "assigned record" in wrong["message"]

        set_count(runner, record_id, count + 1)
        response = reply(runner, response, "incremented", record_id=record_id)
        assert response["status"] == "accepted"

    assert response["completed"] is True
    assert response["instruction"]["text"].startswith("## Done")
    summary = response["instruction"]["context"]["records"]
    assert [item["record"] for item in summary] == [item.id for item in records]
    assert {item["count"] for item in summary} == {2}


def test_scheduler_rejects_a_count_raised_too_far(runner: CliRunner) -> None:
    response = start(runner, "scheduler", {"records": 1, "target": 3})
    record_id = response["instruction"]["context"]["record"]
    set_count(runner, record_id, 2)
    skipped = reply(runner, response, "incremented", record_id=record_id)
    assert skipped["status"] == "rejected"
    assert "is 2; set it to 1" in skipped["message"]
    set_count(runner, record_id, 1)
    accepted = reply(runner, response, "incremented", record_id=record_id)
    assert accepted["instruction"]["context"]["count"] == 1


# A scripted harness: raises the assigned count with the CLI, then replies.
SCHEDULER_AGENT = """
import json, os, subprocess, sys
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
context = response["instruction"]["context"]
if response.get("completed"):
    print("final:", [(item["record"], item["count"]) for item in context["records"]])
    raise SystemExit
command = ["record", "update", context["record"], "--set", f"count={context['next']}"]
subprocess.run([sys.executable, "-m", "hiraku.cli", *command], check=True)
reply = {"reply": "incremented", "arguments": {"record_id": context["record"]}}
Path(os.environ["HIRAKU_REPLY_FILE"]).write_text(json.dumps(reply))
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
    (summary,) = Session.list_all(config=HirakuConfig.discover())
    assert summary["status"] == "completed"
