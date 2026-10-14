from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.cli import main


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


def test_count_steps_tokens_and_gate(runner: CliRunner) -> None:
    listed = invoke(runner, ["workflow", "list"]).output
    assert "count-steps" in listed

    response = start(runner, "count-steps", {"count": 6})
    tokens = []
    for step in range(1, 5):
        instruction = response["instruction"]
        context = instruction["context"]
        assert instruction["text"].startswith("## Step")
        assert context["line"] == f"Step {step}/6: token {context['token']}"
        assert [item["reply"] for item in instruction["replies"]] == ["ack", "finish"]
        tokens.append(context["token"])
        response = reply(runner, response, "ack", token=context["token"])

    gate = response["instruction"]
    assert gate["text"].startswith("## Gate")
    assert gate["requires_user_response"] is True
    assert gate["context"]["remaining"] == 1
    assert [item["reply"] for item in gate["replies"]] == ["proceed", "finish"]
    stale = reply(runner, response, "proceed", token=tokens[-1])
    assert stale["status"] == "rejected"
    assert "does not match step 5" in stale["message"]
    response = reply(runner, response, "proceed", token=gate["context"]["token"])

    assert response["instruction"]["context"]["step"] == 6
    token = response["instruction"]["context"]["token"]
    done = reply(runner, response, "ack", token=token)
    assert done["completed"] is True
    assert done["result"] == {"steps": 6, "count": 6}


def test_count_steps_rejects_wrong_tokens_and_finishes_early(
    runner: CliRunner,
) -> None:
    assert start(runner, "count-steps", {"count": 0})["status"] == "rejected"

    response = start(runner, "count-steps", {"count": 2})
    wrong = reply(runner, response, "ack", token="00000000")
    assert wrong["status"] == "rejected"
    done = reply(runner, response, "finish")
    assert done["result"] == {"steps": 1, "count": 2}
