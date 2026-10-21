from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


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
    project = tmp_path / "workflows"
    project.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True)
    monkeypatch.chdir(project)
    selected = CliRunner()
    invoke(selected, ["init", "--example", "workflows"])
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
        context = instruction["data"]
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
        assert gate["data"] == {"done": step, "remaining": 3 - step}
        gate_options = gate["events"]["accepted"]
        assert [item["event"] for item in gate_options] == ["step", "finish"]
        response = send(runner, response, "step")

    assert response["session_status"] == "completed"
    assert response["instruction"]["text"].startswith("## Done")
    assert response["instruction"]["data"] == {"steps": 3, "count": 3}
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
    gate = send(runner, response, "ack", token=response["instruction"]["data"]["token"])
    done = send(runner, gate, "finish")
    assert done["session_status"] == "completed"
    assert done["instruction"]["data"] == {"steps": 1, "count": 2}


def test_greet_is_one_final_prompt_filled_from_its_arguments(
    runner: CliRunner,
) -> None:
    response = start(runner, "greet", {"name": "Ada", "times": 3})
    assert response["status"] == "accepted"
    assert response["session_status"] == "completed"
    instruction = response["instruction"]
    assert instruction["text"].startswith("# Greet\n\nPrint 3 greetings to Ada")
    assert "Greet formally:\nfalse." in instruction["text"]
    assert instruction["data"] == {"name": "Ada", "times": 3, "formal": False}
    assert "events" not in instruction

    default = start(runner, "greet", {})
    assert default["status"] == "accepted"
    assert default["instruction"]["data"] == {
        "name": "World",
        "times": 2,
        "formal": False,
    }


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
    parent = response["instruction"]["data"]["record"].rsplit("-", 1)[0]
    records = children(parent)
    assert len(records) == 3
    assert all(1 <= item.priority <= 9 and item.count == 0 for item in records)
    # The highest priority runs to the target first; ties go to the larger ID.
    order = sorted(records, key=lambda item: (item.priority, item.id), reverse=True)
    expected = [item.id for item in order for _ in range(2)]

    for done, record_id in enumerate(expected):
        instruction = response["instruction"]
        context = instruction["data"]
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
    summary = response["instruction"]["data"]["records"]
    assert [item["record"] for item in summary] == [item.id for item in records]
    assert {item["count"] for item in summary} == {2}


def test_scheduler_rejects_a_count_raised_too_far(runner: CliRunner) -> None:
    response = start(runner, "scheduler", {"records": 1, "target": 3})
    record_id = response["instruction"]["data"]["record"]
    set_count(runner, record_id, 2)
    skipped = send(runner, response, "incremented", record_id=record_id)
    assert skipped["status"] == "rejected"
    assert "is 2; set it to 1" in skipped["message"]
    set_count(runner, record_id, 1)
    accepted = send(runner, response, "incremented", record_id=record_id)
    assert accepted["instruction"]["data"]["count"] == 1


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
