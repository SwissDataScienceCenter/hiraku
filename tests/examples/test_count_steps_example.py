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
    project = tmp_path / "count-steps"
    project.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True)
    monkeypatch.chdir(project)
    selected = CliRunner()
    invoke(selected, ["init", "--example", "count-steps"])
    assert (project / "hiraku" / "workflows" / "count-steps" / "workflow.py").is_file()
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

    response = start(runner, "count-steps", {"count": 3})
    tokens = []
    for step in range(1, 4):
        instruction = response["instruction"]
        context = instruction["context"]
        assert instruction["text"].startswith("## Step")
        assert context["line"] == f"Step {step}/3: token {context['token']}"
        assert [item["reply"] for item in instruction["replies"]] == ["ack", "finish"]
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
        assert gate["requires_user_response"] is True
        assert gate["context"] == {"done": step, "remaining": 3 - step}
        assert [item["reply"] for item in gate["replies"]] == ["step", "finish"]
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
    done = reply(runner, response, "finish")
    assert done["completed"] is True
    assert done["instruction"]["context"] == {"steps": 1, "count": 2}


# A scripted harness: prints the step line and echoes the step's token.
ECHO_AGENT = """
import json, os
from pathlib import Path

response = json.loads(Path(os.environ["HIRAKU_INSTRUCTION_FILE"]).read_text())
instruction = response["instruction"]
if response.get("completed"):
    print("Ran", instruction["context"]["steps"], "steps")
    raise SystemExit
print(instruction["context"]["line"])
reply = {"reply": instruction["replies"][0]["reply"],
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
