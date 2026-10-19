from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import (
    HirakuError,
    RecordNotFoundError,
    SectionNotFoundError,
    SessionNotFoundError,
    ValidationError,
)
from hiraku.markdown import Markdown
from hiraku.session import Session
from hiraku.workflow import (
    Instruction,
    PromptWorkflow,
    Workflow,
    data_model,
    discover_workflows,
    event_methods,
    list_workflows,
    load_workflow,
    load_workflows,
    start_model,
    start_state,
    state_methods,
    workflow_description,
    workflow_graph,
)

TOY_PY = '''\
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from hiraku.errors import EventRejected, NotReady
from hiraku.workflow import Instruction, Workflow, event, state

from .helpers import scale


class Total(BaseModel):
    target: int
    total: int = 0


class Toy(Workflow):
    """Add numbers until a target is reached.

    Used to exercise the workflow runtime.
    """

    step: int = 1

    def start(
        self, target: Annotated[int, Field(gt=0, description="Sum to reach.")] = 3
    ) -> Total:
        """Start adding."""
        return Total(target=target)

    @state("adding", start=True)
    def adding(self, total: Total) -> Instruction:
        return Instruction(
            self.markdown.section("## Add"),
            context={"total": total.total},
            events=[self.add, self.wait, self.boom],
        )

    @state("done", final=True)
    def done(self, total: Total) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"), context={"total": total.total}
        )

    @event(source="adding", dest=["adding", "done"])
    def add(self, total: Total, amount: int) -> str:
        """Add an amount."""
        if amount < 0:
            raise EventRejected("amount must not be negative")
        total.total += scale(amount) * self.step
        return "done" if total.total >= total.target else "adding"

    @event(source="adding", dest="adding")
    def wait(self, total: Total) -> None:
        """Wait for evidence."""
        total.total = -100
        raise NotReady("evidence is not there yet")

    @event(source="adding", dest="adding")
    def boom(self, total: Total) -> None:
        """Fail unexpectedly."""
        total.total = -100
        raise RuntimeError("kaboom")

    @event(source="adding", dest="adding")
    def hidden(self, total: Total) -> None:
        """Never offered."""


WORKFLOW = Toy
'''

TOY_MD = (
    "# Toy\n\nAdd numbers.\n\n## Add\n\nAdd a number.\n\n## Done\n\nReport the total.\n"
)

# Minimal valid parts, for sources that test one invalid part.
MINIMAL = (
    "from pydantic import BaseModel\n"
    "from hiraku.workflow import Instruction, Workflow, event, state\n"
    "class S(BaseModel):\n    pass\n"
)
STATES = (
    "    @state('go', start=True)\n"
    "    def go(self, data: S) -> Instruction: ...\n"
    "    @state('end', final=True)\n"
    "    def end(self, data: S) -> Instruction: ...\n"
    "    @event(source='go', dest='end')\n"
    "    def stop(self, data: S) -> None: ...\n"
)


def document(workflow_id: str, body: str = "") -> str:
    return f"---\nid: {workflow_id}\n---\n{body}"


def write_workflow(
    config: HirakuConfig, workflow_id: str, source: str, md: str = ""
) -> Path:
    directory = config.workflows_dir / workflow_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workflow.py").write_text(source, encoding="utf-8")
    (directory / "workflow.md").write_text(document(workflow_id, md), encoding="utf-8")
    return directory


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    directory = write_workflow(selected, "toy", TOY_PY, TOY_MD)
    (directory / "helpers.py").write_text("def scale(value):\n    return value\n")
    return selected


def load_error(config: HirakuConfig, workflow_id: str) -> str:
    with pytest.raises(HirakuError) as info:
        load_workflow(workflow_id, config=config)
    return str(info.value)


def toy(config: HirakuConfig) -> Workflow:
    return load_workflow("toy", config=config).bind(config)


def started(workflow: Workflow, target: int = 3) -> dict:
    response = workflow.run_start({"target": target})
    assert response["status"] == "accepted", response
    return response


def send(workflow: Workflow, name: str, response: dict, **arguments) -> dict:
    return workflow.run_event(
        response["session_id"], response["instruction_id"], name, arguments
    )


def stored(config: HirakuConfig, session_id: str) -> dict:
    return json.loads((config.sessions_dir / f"{session_id}.json").read_text())


def test_start_and_event_signatures_and_relative_imports(
    config: HirakuConfig,
) -> None:
    workflow = toy(config)
    assert workflow_description(workflow).startswith("Add numbers until a target")
    start = start_model(type(workflow)).model_json_schema()["properties"]["target"]
    assert start["default"] == 3
    assert start["description"] == "Sum to reach."
    assert start["exclusiveMinimum"] == 0
    assert workflow.start_arguments() is start_model(type(workflow))
    assert list(event_methods(type(workflow))) == ["add", "wait", "boom", "hidden"]
    assert list(state_methods(type(workflow))) == ["adding", "done"]
    assert start_state(type(workflow)) == "adding"


def test_start_states_and_events_stay_directly_callable(config: HirakuConfig) -> None:
    workflow = toy(config)
    total = workflow.start(target=5)
    assert type(total) is data_model(type(workflow))
    assert workflow.add(total, amount=2) == "adding"
    assert total.total == 2
    instruction = workflow.adding(total)
    assert isinstance(instruction, Instruction)
    assert instruction.events == [workflow.add, workflow.wait, workflow.boom]
    assert instruction.context == {"total": 2}
    assert workflow.add(total, amount=3) == "done"


ADDING = """\
        return Instruction(
            self.markdown.section("## Add"),
            context={"total": total.total},
            events=[self.add, self.wait, self.boom],
        )
"""
DONE = """\
        return Instruction(
            self.markdown.section("## Done"), context={"total": total.total}
        )
"""


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            ADDING,
            "        return Instruction('Ask', events=[self.adding])\n",
            "must be @event methods",
        ),
        (
            ADDING,
            "        return Instruction('Ask', events=[self.add, self.add])\n",
            "same event more than once",
        ),
        (ADDING, "        return Instruction(' ')\n", "must not be empty"),
        (
            ADDING,
            "        return Instruction('Ask', context=object())\n",
            "must be JSON-compatible",
        ),
        (ADDING, "        return 'Ask'\n", "it must return an Instruction"),
        (
            ADDING,
            "        total.total = 1\n        return Instruction('Ask')\n",
            "State 'adding' of workflow 'toy' changed the data",
        ),
        (
            DONE,
            "        return Instruction('Done', events=[self.add])\n",
            "final state 'done' accepts no events",
        ),
        (
            DONE,
            "        return Instruction('Done', requires_user_response=True)\n",
            "final state 'done' accepts no events and requires no user response",
        ),
    ],
)
def test_invalid_instructions_are_errors_and_save_nothing(
    config: HirakuConfig, old: str, new: str, message: str
) -> None:
    assert old in TOY_PY
    (config.workflows_dir / "toy" / "workflow.py").write_text(TOY_PY.replace(old, new))
    workflow = toy(config)
    response = workflow.run_start({"target": 1 if old is DONE else 3})
    if old is DONE:
        assert response["status"] == "accepted"
        response = send(workflow, "add", response, amount=1)
    assert response["status"] == "error"
    assert message in response["message"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '        return "done" if total.total >= total.target else "adding"\n',
            '        return "elsewhere"\n',
            "must return the state it moves to, one of adding, done; got 'elsewhere'",
        ),
        (
            '        return "done" if total.total >= total.target else "adding"\n',
            "        pass\n",
            "must return the state it moves to, one of adding, done; got None",
        ),
    ],
)
def test_events_with_several_destinations_return_one(
    config: HirakuConfig, old: str, new: str, message: str
) -> None:
    (config.workflows_dir / "toy" / "workflow.py").write_text(TOY_PY.replace(old, new))
    workflow = toy(config)
    first = started(workflow)
    response = send(workflow, "add", first, amount=1)
    assert response["status"] == "error"
    assert message in response["message"]
    assert stored(config, first["session_id"])["data"] == {"target": 3, "total": 0}


def test_events_with_one_destination_return_nothing(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        '        """Never offered."""\n',
        '        """Never offered."""\n        return "done"\n',
    ).replace("events=[self.add, self.wait, self.boom]", "events=[self.hidden]")
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    workflow = toy(config)
    response = send(workflow, "hidden", started(workflow))
    assert response["status"] == "error"
    assert "it moves to 'adding' and returns nothing" in response["message"]


def test_start_must_return_the_data(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        "        return Total(target=target)", "        return {'target': target}"
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    response = toy(config).run_start({"target": 3})
    assert response["status"] == "error"
    assert "produced dict data; start must return a Total" in response["message"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("x = 1\n", "must export WORKFLOW"),
        (
            "from hiraku.workflow import Workflow\n"
            "class W(Workflow):\n    pass\n"
            "WORKFLOW = W\n",
            "must define a start method",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self): ...\n" + STATES + "WORKFLOW = W\n",
            "must declare the workflow's data as its return type",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> int: ...\n" + STATES + "WORKFLOW = W\n",
            "must declare the workflow's data as its return type",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            "WORKFLOW = W\n",
            "declares no states",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            + STATES.replace("def go(self, data: S)", "def go(self, data: S, x: int)")
            + "WORKFLOW = W\n",
            "@state W.go must take only the workflow's data",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> None: ...\n" + STATES + "WORKFLOW = W\n",
            "@state W.go must take no parameters; the workflow has no data",
        ),
        (
            MINIMAL + "class T(BaseModel):\n    pass\n"
            "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            + STATES.replace("def stop(self, data: S)", "def stop(self, data: T)")
            + "WORKFLOW = W\n",
            "@event W.stop must take the workflow's data as its first parameter, "
            "annotated with S",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self, *values: int) -> S: ...\n" + STATES + "WORKFLOW = W\n",
            "cannot use",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self, untyped) -> S: ...\n" + STATES + "WORKFLOW = W\n",
            "needs a type annotation",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            + STATES.replace("@state('end'", "@state('bad-name'")
            + "WORKFLOW = W\n",
            "State names use letters, digits, and '_'",
        ),
        ("raise ImportError('broken helper')\n", "broken helper"),
    ],
)
def test_invalid_workflows_are_logged_and_left_out(
    config: HirakuConfig,
    source: str,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_workflow(config, "bad", source)
    workflows = load_workflows(config)
    assert "Workflow bad is unavailable" in caplog.text
    assert message in caplog.text
    assert "toy" in workflows
    assert "bad" not in workflows
    assert message in load_error(config, "bad")


def test_workflow_ids_are_letters_digits_and_hyphens(config: HirakuConfig) -> None:
    source = (
        MINIMAL + "class W(Workflow):\n"
        "    def start(self) -> S: ...\n" + STATES + "WORKFLOW = W\n"
    )
    for identifier in ("bad.name", "x_y"):
        write_workflow(config, identifier, source)
        assert "Invalid workflow ID" in load_error(config, identifier)
    assert set(load_workflows(config)) == {"collaborative", "toy"}


def test_prompts_are_snapshotted_with_the_code(config: HirakuConfig) -> None:
    workflow = toy(config)
    (config.workflows_dir / "toy" / "workflow.md").write_text(
        document("toy", "# Changed\n\n## Add\n\nNew\n")
    )
    assert workflow.markdown.section("## Add") == "## Add\n\nAdd a number."
    assert toy(config).markdown.section("## Add") == "## Add\n\nNew"


def test_start_returns_the_first_section_and_explicit_events(
    config: HirakuConfig,
) -> None:
    response = started(toy(config))
    # The text before the first section is not sent; instructions stand alone.
    assert "orientation" not in response
    assert response["instruction_id"] == 1
    instruction = response["instruction"]
    assert instruction["text"] == "## Add\n\nAdd a number."
    assert instruction["context"] == {"total": 0}
    events_block = instruction["events"]
    assert events_block["session_id"] == response["session_id"]
    assert events_block["instruction_id"] == 1
    assert events_block["requires_user_response"] is False
    accepted = events_block["accepted"]
    assert [item["event"] for item in accepted] == ["add", "wait", "boom"]
    assert accepted[0]["description"] == "Add an amount."
    assert accepted[0]["arguments"] == {
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
    }
    saved = stored(config, response["session_id"])
    assert saved["data"] == {"target": 3, "total": 0}
    assert saved["workflow"] == "toy"
    assert saved["instruction_id"] == 1
    assert saved["events"] == ["add", "wait", "boom"]
    assert (config.sessions_dir / ".gitignore").read_text() == "*\n"


def test_start_validation_errors_create_no_session(config: HirakuConfig) -> None:
    response = toy(config).run_start({"target": 0})
    assert response["status"] == "rejected"
    assert "target" in response["message"]
    assert Session.list_all(config=config) == []


def test_correlation_table(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=5)
    accepted = send(workflow, "add", first, amount=2)
    assert accepted["status"] == "accepted"
    assert accepted["instruction_id"] == 2
    assert accepted["instruction"]["context"] == {"total": 2}

    retry = send(workflow, "add", first, amount=2)
    assert retry == accepted

    conflict = send(workflow, "add", first, amount=1)
    assert conflict["status"] == "stale"
    assert "already answered" in conflict["message"]
    assert conflict["instruction"]["context"] == {"total": 2}

    future = workflow.run_event(first["session_id"], 9, "add", {"amount": 1})
    assert future["status"] == "stale"
    assert "pending instruction is 2" in future["message"]

    not_offered = send(workflow, "hidden", accepted)
    assert not_offered["status"] == "rejected"
    assert "not an event the current instruction accepts" in not_offered["message"]

    unknown = send(workflow, "ask", accepted)
    assert unknown["status"] == "rejected"
    assert "not an event of workflow 'toy'" in unknown["message"]
    assert "accepts: add, wait, boom" in unknown["message"]

    second = send(workflow, "add", accepted, amount=1)
    stale = send(workflow, "add", first, amount=5)
    assert second["instruction_id"] == 3
    assert stale["status"] == "stale"
    assert stale["instruction_id"] == 3
    assert stored(config, first["session_id"])["data"]["total"] == 3


def test_final_instruction_and_retried_completion(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=2)
    done = send(workflow, "add", first, amount=2)
    assert done == {
        "status": "accepted",
        "session_id": first["session_id"],
        "instruction_id": 2,
        "completed": True,
        "instruction": {
            "text": "## Done\n\nReport the total.",
            "context": {"total": 2},
        },
    }
    assert send(workflow, "add", first, amount=2) == done
    after = workflow.run_event(first["session_id"], 2, "add", {"amount": 1})
    assert after["status"] == "rejected"
    assert "completed" in after["message"]
    shown = Session.load(first["session_id"], config=config).show()
    assert shown["status"] == "completed"


def test_rejections_and_not_ready_save_nothing(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    schema = send(workflow, "add", first, amount="many")
    assert schema["status"] == "rejected"
    assert "amount" in schema["message"]
    rejected = send(workflow, "add", first, amount=-1)
    assert rejected["status"] == "rejected"
    assert rejected["message"] == "amount must not be negative"
    not_ready = send(workflow, "wait", first)
    assert not_ready["status"] == "not_ready"
    assert not_ready["instruction_id"] == 1
    assert not_ready["instruction"]["text"] == "## Add\n\nAdd a number."
    error = send(workflow, "boom", first)
    assert error["status"] == "error"
    assert "RuntimeError: kaboom" in error["message"]
    assert error["instruction_id"] == 1
    assert path.read_text() == before
    assert send(workflow, "add", first, amount=1)["status"] == "accepted"


def test_invalid_correlation_arguments(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    with pytest.raises(ValidationError, match="Invalid session ID"):
        workflow.run_event("../x", 1, "add")
    with pytest.raises(ValidationError, match="instruction_id"):
        workflow.run_event(first["session_id"], None, "add", {"amount": 1})  # type: ignore[arg-type]
    with pytest.raises(SessionNotFoundError):
        workflow.run_event("missing", 1, "add")


def test_restart_recovery_continues_from_disk(config: HirakuConfig) -> None:
    first = started(toy(config), target=4)
    restarted = load_workflow("toy", config=HirakuConfig.discover(config.root))
    shown = Session.load(first["session_id"], config=config).show(restarted)
    assert shown["response"]["instruction"] == first["instruction"]
    assert send(restarted.bind(config), "add", first, amount=1)["status"] == (
        "accepted"
    )


def test_concurrent_events_apply_once(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=10)
    barrier = threading.Barrier(4)
    results: list[dict] = []

    def worker() -> None:
        barrier.wait()
        results.append(send(workflow, "add", first, amount=1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item["status"] for item in results} == {"accepted"}
    assert {item["instruction_id"] for item in results} == {2}
    assert stored(config, first["session_id"])["data"]["total"] == 1


def test_failed_save_keeps_previous_state(config: HirakuConfig, monkeypatch) -> None:
    workflow = toy(config)
    first = started(workflow)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.session.atomic_write", fail)
    with pytest.raises(ValidationError, match="disk full"):
        send(workflow, "add", first, amount=1)
    monkeypatch.undo()
    assert path.read_text() == before
    assert send(workflow, "add", first, amount=1)["status"] == "accepted"


def test_incompatible_sessions_are_rejected_but_inspectable(
    config: HirakuConfig,
) -> None:
    first = started(toy(config))
    path = config.workflows_dir / "toy" / "workflow.py"
    path.write_text(
        TOY_PY.replace("    total: int = 0\n", "    total: int = 0\n    new: str\n")
    )
    workflow = toy(config)
    rejected = send(workflow, "add", first, amount=1)
    assert rejected["status"] == "rejected"
    assert "stored data no longer matches" in rejected["message"]
    shown = Session.load(first["session_id"], config=config).show(workflow)
    assert "new" in shown["compatibility"]

    path.write_text(TOY_PY.replace("    def wait(", "    def waiting("))
    shown = Session.load(first["session_id"], config=config).show(toy(config))
    assert "pending events wait" in shown["compatibility"]

    path.write_text(TOY_PY.replace('"adding"', '"summing"'))
    shown = Session.load(first["session_id"], config=config).show(toy(config))
    assert "no longer declares the state 'adding'" in shown["compatibility"]


def test_inspection_and_cancellation_without_the_workflow(
    config: HirakuConfig,
) -> None:
    first = started(toy(config))
    (config.workflows_dir / "toy" / "workflow.py").write_text("raise RuntimeError\n")
    shown = Session.load(first["session_id"], config=config).show()
    assert "unavailable" in shown["note"]
    assert shown["data"] == {"target": 3, "total": 0}
    cancelled = Session.load(first["session_id"], config=config).cancel()
    assert cancelled["status"] == "cancelled"
    assert "does not revert code" in cancelled["note"]
    shown = Session.load(first["session_id"], config=config).show()
    assert shown["status"] == "cancelled"
    assert "no longer actionable" in shown["note"]
    assert shown["data"] == {"target": 3, "total": 0}
    again = Session.load(first["session_id"], config=config).cancel()
    assert again["status"] == "cancelled"


def test_cancelled_sessions_reject_events(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    Session.load(first["session_id"], config=config).cancel()
    response = send(workflow, "add", first, amount=1)
    assert response["status"] == "rejected"
    assert "cancelled" in response["message"]


def test_cannot_cancel_completed_session(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=1)
    send(workflow, "add", first, amount=1)
    with pytest.raises(ValidationError, match="already completed"):
        Session.load(first["session_id"], config=config).cancel()


def test_malformed_session_files(config: HirakuConfig) -> None:
    started(toy(config))
    (config.sessions_dir / "broken.json").write_text("{not json")
    listing = Session.list_all(config=config)
    assert {"session_id": "broken", "error": listing[-1]["error"]} in listing
    with pytest.raises(ValidationError, match="malformed"):
        Session.load("broken", config=config).show()


def test_workflow_list_describes_every_loaded_workflow(config: HirakuConfig) -> None:
    listed = list_workflows(load_workflows(config))
    assert [item["id"] for item in listed] == [
        "collaborative",
        "toy",
    ]
    assert listed[-1]["description"].startswith("Add numbers until a target")


def test_cli_matches_the_runtime(
    config: HirakuConfig, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    arguments = tmp_path / "arguments.json"
    arguments.write_text(json.dumps({"target": 2}))
    result = runner.invoke(
        main, ["workflow", "start", "toy", "--arguments-file", str(arguments)]
    )
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["status"] == "accepted"
    session_id = response["session_id"]
    assert response == {
        **Session.load(session_id, config=config).show()["response"],
        "status": "accepted",
    }

    result = runner.invoke(
        main,
        ["workflow", "send-event", session_id, "add", "--instruction-id", "1"],
    )
    assert json.loads(result.output)["status"] == "rejected"
    result = runner.invoke(
        main,
        [
            "workflow",
            "send-event",
            session_id,
            "add",
            "--instruction-id",
            "1",
            "--arguments-file",
            "-",
        ],
        input=json.dumps({"amount": 1}),
    )
    assert json.loads(result.output)["status"] == "accepted"

    listing = runner.invoke(main, ["workflow", "list", "--json"])
    assert {item["id"] for item in json.loads(listing.output)} >= {"toy"}
    shown = runner.invoke(main, ["session", "show", session_id])
    assert "Pending instruction 2" in shown.output
    assert "Events: add, wait, boom" in shown.output
    sessions = json.loads(runner.invoke(main, ["session", "list", "--json"]).output)
    assert sessions[0]["session_id"] == session_id
    cancelled = runner.invoke(main, ["session", "cancel", session_id])
    assert "cancelled" in cancelled.output

    done = toy(config)
    first = started(done, target=1)
    send(done, "add", first, amount=1)
    shown = runner.invoke(main, ["session", "show", first["session_id"]])
    assert "Final instruction 2:" in shown.output
    assert "Report the total." in shown.output
    assert '"total": 1' in shown.output
    assert "Replies:" not in shown.output
    unknown = runner.invoke(main, ["workflow", "start", "nope"])
    assert unknown.exit_code != 0
    assert "Unknown workflow" in unknown.output


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "    @event(source='go', dest='end')\n"
            "    def save(self, data: S) -> None: ...\n",
            "@event W.save is named like a Workflow method",
        ),
        (
            "    @event(source='go', dest='end')\n"
            "    def run_event(self, data: S) -> None: ...\n",
            "is named like a Workflow method",
        ),
        ("    LIMIT = 3\n", "non-annotated attribute"),
    ],
)
def test_invalid_event_names_and_attributes(
    config: HirakuConfig, body: str, message: str
) -> None:
    source = (
        MINIMAL + "class W(Workflow):\n"
        "    def start(self) -> S: ...\n" + STATES + f"{body}"
        "WORKFLOW = W\n"
    )
    write_workflow(config, "named", source)
    assert message in load_error(config, "named")


def test_front_matter_settings_are_typed(config: HirakuConfig) -> None:
    path = config.workflows_dir / "toy" / "workflow.md"
    path.write_text("---\nid: toy\nstep: 2\nnote: kept\n---\n" + TOY_MD)
    workflow = toy(config)
    first = started(workflow, target=10)
    assert send(workflow, "add", first, amount=3)["instruction"]["context"] == {
        "total": 6
    }
    assert workflow.to_dict()["note"] == "kept"

    path.write_text("---\nid: toy\nstep: many\n---\n" + TOY_MD)
    error = load_error(config, "toy")
    assert "Invalid workflow settings" in error
    assert "step" in error


def test_bound_copies_are_isolated(config: HirakuConfig) -> None:
    loaded = load_workflow("toy", config=config)
    first = loaded.bind(config)
    assert first.id == "toy"
    assert first.config is config
    first.step = 5
    first.markdown = "# Changed\n"
    second = loaded.bind(config)
    assert second.step == 1
    assert second.markdown.section("## Add") == "## Add\n\nAdd a number."
    assert (
        not (config.workflows_dir / "toy" / "workflow.md")
        .read_text()
        .startswith("# Changed")
    )


def test_markdown_only_workflows_send_one_final_instruction(
    config: HirakuConfig,
) -> None:
    directory = config.workflows_dir / "loop"
    directory.mkdir()
    body = (
        "# Loop\n\nRun {{iterations}} experiments on {{task}} (quick: {{quick}}).\n"
        "\n## Steps\n\nDo things.\n"
    )
    (directory / "workflow.md").write_text(
        "---\nid: loop\ndescription: Run the loop.\narguments:\n"
        "  task: {required: true}\n"
        "  iterations: {type: integer, default: 5}\n"
        "  quick: {type: boolean, default: false}\n"
        "---\n" + body
    )
    loaded = load_workflows(config)["loop"]
    assert type(loaded) is PromptWorkflow
    assert workflow_description(loaded) == "Run the loop."
    assert event_methods(type(loaded)) == {}

    workflow = loaded.bind(config)
    first = workflow.run_start({"task": "sep10", "iterations": "3"})
    assert first["status"] == "accepted"
    assert first["instruction"]["text"].startswith(
        "# Loop\n\nRun 3 experiments on sep10 (quick: false)."
    )
    assert first["instruction"]["context"] == {
        "task": "sep10",
        "iterations": 3,
        "quick": False,
    }
    assert first["completed"] is True
    assert "events" not in first["instruction"]
    after = send(workflow, "complete", first, summary="Tried three ideas")
    assert after["status"] == "rejected"
    stored = Session.load(first["session_id"], config=config)
    assert stored.status == "completed"
    assert stored.show()["data"] == {
        "arguments": {"task": "sep10", "iterations": 3, "quick": False}
    }
    assert [(item.event, item.instruction_id) for item in stored.history] == [
        ("start", 0)
    ]

    missing = workflow.run_start({})
    assert missing["status"] == "rejected"
    assert "task" in missing["message"]
    unknown = workflow.run_start({"task": "x", "other": 1})
    assert unknown["status"] == "rejected"

    (directory / "workflow.md").write_text("---\nid: loop\n---\nUse {{nope}}.\n")
    assert "placeholders without declared arguments: nope" in load_error(config, "loop")
    (directory / "workflow.md").write_text(body)
    assert "front matter" in load_error(config, "loop")


# workflow.md: sections, placeholders, and the record -----------------------


DOCUMENT = """\
# Research

Introduction text.

### Not a section boundary

Still introduction.

## Select

Pick one.

### Details

Nested detail.

```markdown
## Not a heading
```

## Approve the Launch!

Ask first.
"""


def research() -> Workflow:
    return Workflow(id="research", markdown=DOCUMENT)


def test_headings_are_top_level_sections_with_their_lines() -> None:
    assert [str(item) for item in Markdown(DOCUMENT).headings] == [
        "# Research",
        "### Not a section boundary",
        "## Select",
        "### Details",
        "## Approve the Launch!",
    ]
    nested = Markdown("- item\n\n  ## In a list\n\n> ## Quoted\n")
    assert nested.headings == ()


def test_first_level_section_contains_the_whole_document() -> None:
    document = research().markdown.section("# Research")
    assert document.startswith("# Research\n\nIntroduction text.")
    assert document.endswith("Ask first.")


def test_section_includes_subsections_and_ignores_fenced_headings() -> None:
    markdown = research().markdown
    select = markdown.section("## Select")
    assert select.startswith("## Select")
    assert "Nested detail." in select
    assert "## Not a heading" in select
    assert "Ask first." not in select
    details = markdown.section("### Details")
    assert details.startswith("### Details\n\nNested detail.")
    assert details.endswith("```")
    assert markdown.section("## Approve the Launch!").endswith("Ask first.")
    assert markdown.section("  ##  Approve  the Launch! ##").endswith("Ask first.")
    with pytest.raises(SectionNotFoundError):
        markdown.section("## Not a heading")


def test_section_level_must_match() -> None:
    with pytest.raises(SectionNotFoundError, match="headings: '# Research'"):
        research().markdown.section("## Details")


@pytest.mark.parametrize("selector", ["select", "Select", "#Select", "####### X", "##"])
def test_section_selector_must_be_a_heading_line(selector: str) -> None:
    with pytest.raises(ValidationError, match="Select a section by its heading"):
        research().markdown.section(selector)


def test_underlined_headings_are_selected_as_heading_lines() -> None:
    markdown = Markdown("Title\n=====\n\nIntro.\n\nStep\n----\n\nDo it.\n")
    assert markdown.section("## Step") == "Step\n----\n\nDo it."
    assert markdown.section("# Title").startswith("Title\n=====\n\nIntro.")


def test_section_lines_follow_markdown_line_endings() -> None:
    markdown = Markdown("# A\r\n\r\n## B\r\nb \u2028 still b\r\n## C\r\n")
    assert markdown.section("## B") == "## B\nb \u2028 still b"


def test_records_may_repeat_headings_but_selecting_one_is_ambiguous() -> None:
    markdown = Markdown("## Notes\n\nA\n\n## Notes\n\nB\n")
    with pytest.raises(ValidationError, match="Several sections"):
        markdown.section("## Notes")


def test_markdown_field_stays_a_string() -> None:
    record = Workflow(id="doc", markdown="## Old\n")
    record.markdown = "## New\n"
    assert isinstance(record.markdown, Markdown)
    assert record.markdown == "## New\n"
    assert type(record.model_dump()["markdown"]) is str


def test_whole_document_text() -> None:
    assert research().text == DOCUMENT.strip()


def test_missing_section() -> None:
    with pytest.raises(SectionNotFoundError, match="'## Missing' not found"):
        research().markdown.section("## Missing")


def test_workflow_headings_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="several sections '## Select'"):
        Workflow(id="dup", markdown="## Select\n\n## Select\n")
    Workflow(id="levels", markdown="## Select\n\n### Select\n\n## select\n")
    document = Workflow(id="ok", markdown="## Select\n")
    with pytest.raises(ValidationError, match="several sections '## A'"):
        document.markdown = "## A\n\n## A\n"


def test_sections_follow_markdown_changes() -> None:
    document = Workflow(id="doc", markdown="## Old\n\nText\n")
    document.markdown = "# Intro\n\n## New\n\nText\n"
    assert [str(item) for item in document.markdown.headings] == [
        "# Intro",
        "## New",
    ]


def test_workflow_md_is_a_registry_record(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    document = load_workflow("collaborative", config=config)
    assert document.id == "collaborative"
    assert document.path == config.workflows_dir / "collaborative" / "workflow.md"
    assert document.text.startswith("# Collaborative research")
    assert "id: collaborative" not in document.text
    assert discover_workflows(config) == ["collaborative"]

    document.summary = "Bounded research"
    document.save()
    reloaded = load_workflow("collaborative", config=config)
    assert reloaded.to_dict() == {
        "id": "collaborative",
        "description": document.to_dict()["description"],
        "rules": document.rules,
        "summary": "Bounded research",
    }
    assert reloaded.rules.endswith("Never delete records or rewrite Git history.")
    assert reloaded.markdown.section("## Draft") == document.markdown.section(
        "## Draft"
    )


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        ("---\nid: other\n---\n# X\n", ValidationError, "must match the workflow"),
        ("# No front matter\n", ValidationError, "front matter"),
        (None, RecordNotFoundError, "not found"),
    ],
)
def test_invalid_workflow_documents_disable_the_workflow(
    tmp_path: Path, content: str | None, error: type[Exception], message: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    path = config.workflows_dir / "collaborative" / "workflow.md"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    with pytest.raises(error, match=message):
        load_workflow("collaborative", config=config)
    assert load_workflows(config) == {}


# State machines: states, events, and graphs -------------------------------------

MACHINE_PY = '''\
from __future__ import annotations

from pydantic import BaseModel

from hiraku.workflow import Instruction, Workflow, event, state


class Value(BaseModel):
    value: int = 0


class Machine(Workflow):
    def start(self) -> Value:
        return Value()

    @state("ask", start=True)
    def asking(self, value: Value) -> Instruction:
        return Instruction("Ask.")

    @state("check")
    def checking(self, value: Value) -> Instruction:
        return Instruction("Check.")

    @state("done", final=True)
    def finishing(self, value: Value) -> Instruction:
        return Instruction("Done.", context={"value": value.value})

    @event(source="ask", dest="check")
    def give(self, value: Value, number: int) -> None:
        """Give a number."""
        value.value = number

    @event(source="check", dest=["ask", "done"])
    def judge(self, value: Value, ok: bool) -> str:
        """Accept the number, or ask again."""
        return "done" if ok else "ask"

    @event(source=["ask", "check"], dest="done")
    def stop(self, value: Value) -> None:
        """Stop."""


WORKFLOW = Machine
'''


def machine(config: HirakuConfig, source: str = MACHINE_PY) -> Workflow:
    write_workflow(config, "machine", source, "# Machine\n")
    return load_workflow("machine", config=config).bind(config)


def accepted(response: dict) -> list[str]:
    return [item["event"] for item in response["instruction"]["events"]["accepted"]]


def test_the_runtime_moves_between_declared_states(config: HirakuConfig) -> None:
    workflow = machine(config)
    first = workflow.run_start({})
    assert first["status"] == "accepted"
    assert first["instruction"]["text"] == "Ask."
    assert accepted(first) == ["give", "stop"]
    early = send(workflow, "judge", first, ok=True)
    assert early["status"] == "rejected"
    assert "not an event the current instruction accepts" in early["message"]

    checking = send(workflow, "give", first, number=3)
    assert checking["instruction"]["text"] == "Check."
    assert accepted(checking) == ["judge", "stop"]
    assert Session.load(first["session_id"], config=config).state == "check"
    again = send(workflow, "judge", checking, ok=False)
    assert accepted(again) == ["give", "stop"]
    checking = send(workflow, "give", again, number=4)
    done = send(workflow, "judge", checking, ok=True)
    assert done["completed"] is True
    assert done["instruction"] == {"text": "Done.", "context": {"value": 4}}
    stored_session = Session.load(first["session_id"], config=config)
    assert (stored_session.state, stored_session.data) == ("done", {"value": 4})
    assert Session.list_all(config=config)[0]["state"] == "done"


def test_instructions_may_narrow_the_events_of_a_state(config: HirakuConfig) -> None:
    workflow = machine(
        config,
        MACHINE_PY.replace(
            '        return Instruction("Ask.")\n',
            '        return Instruction("Only stop.", events=[self.stop])\n',
        ),
    )
    assert accepted(workflow.run_start({})) == ["stop"]
    wrong = machine(
        config,
        MACHINE_PY.replace(
            '        return Instruction("Ask.")\n',
            '        return Instruction("Ask.", events=[self.judge])\n',
        ),
    ).run_start({})
    assert wrong["status"] == "error"
    assert (
        "State 'ask' does not accept judge; it accepts give, stop" in wrong["message"]
    )


def test_a_final_start_state_completes_the_session_when_it_starts(
    config: HirakuConfig,
) -> None:
    source = (
        "from hiraku.workflow import Instruction, Workflow, state\n"
        "class Once(Workflow):\n"
        "    def start(self) -> None:\n"
        "        return None\n"
        "    @state('done', start=True, final=True)\n"
        "    def done(self) -> Instruction:\n"
        "        return Instruction('Say hello.')\n"
        "WORKFLOW = Once\n"
    )
    write_workflow(config, "once", source, "# Once\n")
    response = load_workflow("once", config=config).bind(config).run_start({})
    assert response["completed"] is True
    assert response["instruction"] == {"text": "Say hello.", "context": None}
    stored_session = Session.load(response["session_id"], config=config)
    assert (stored_session.state, stored_session.data) == ("done", None)


DATALESS_PY = '''\
from hiraku.workflow import Instruction, Workflow, event, state


class Approve(Workflow):
    def start(self) -> None:
        """Ask for an approval."""

    @state("asking", start=True)
    def asking(self) -> Instruction:
        return Instruction("Ask the user.", requires_user_response=True)

    @state("approved", final=True)
    def approved(self) -> Instruction:
        return Instruction("Thank the user.")

    @event(source="asking", dest="approved")
    def approve(self, note: str = "") -> None:
        """Approve."""


WORKFLOW = Approve
'''


def test_workflows_without_data(config: HirakuConfig) -> None:
    write_workflow(config, "approve", DATALESS_PY, "# Approve\n")
    workflow = load_workflow("approve", config=config).bind(config)
    first = workflow.run_start({})
    assert first["instruction"]["events"]["requires_user_response"] is True
    done = send(workflow, "approve", first, note="fine")
    assert done["completed"] is True
    assert Session.load(first["session_id"], config=config).data is None


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '    @state("check")\n',
            '    @state("check", start=True)\n',
            "needs exactly one state with start=True; it has ask, check",
        ),
        (
            '    @state("ask", start=True)\n',
            '    @state("ask")\n',
            "needs exactly one state with start=True; it has none",
        ),
        (
            '    @state("done", final=True)\n',
            '    @state("done")\n',
            "needs a state with final=True",
        ),
        (
            '    @event(source="ask", dest="check")\n',
            '    @event(source="ask", dest=["check", "nowhere"])\n',
            "names unknown states nowhere; states are ask, check, done",
        ),
        (
            '    @event(source=["ask", "check"], dest="done")\n',
            '    @event(source=["ask", "check", "done"], dest="done")\n',
            "Final state 'done' of Machine accepts stop; a final state accepts no "
            "events",
        ),
        (
            "WORKFLOW = Machine\n",
            "    @state('idle')\n"
            "    def idle(self, value: Value) -> Instruction: ...\n"
            "WORKFLOW = Machine\n",
            "State 'idle' of Machine accepts no events",
        ),
        (
            "WORKFLOW = Machine\n",
            "    @state('lost')\n"
            "    def lost(self, value: Value) -> Instruction: ...\n"
            "    @event(source='lost', dest='done')\n"
            "    def leave(self, value: Value) -> None: ...\n"
            "WORKFLOW = Machine\n",
            "States of Machine cannot be reached from 'ask': lost",
        ),
        (
            "WORKFLOW = Machine\n",
            "    @state('ask')\n"
            "    def again(self, value: Value) -> Instruction: ...\n"
            "WORKFLOW = Machine\n",
            "State 'ask' of Machine is declared twice, by asking and again",
        ),
    ],
)
def test_inconsistent_state_machines_fail_to_load(
    config: HirakuConfig, old: str, new: str, message: str
) -> None:
    assert old in MACHINE_PY
    write_workflow(config, "machine", MACHINE_PY.replace(old, new, 1), "# Machine\n")
    assert message in load_error(config, "machine")


def test_states_and_events_are_drawn_as_a_mermaid_state_diagram(
    config: HirakuConfig,
) -> None:
    workflow = machine(config)
    assert workflow_graph(workflow, current="check") == (
        "stateDiagram-v2\n"
        "  [*] --> ask\n"
        "  ask --> check: give\n"
        "  check --> ask: judge\n"
        "  check --> done: judge\n"
        "  ask --> done: stop\n"
        "  check --> done: stop\n"
        "  done --> [*]\n"
        "  classDef current fill:#fdff32\n"
        "  class check current\n"
    )
    assert workflow_graph(toy(config)).startswith("stateDiagram-v2\n  [*] --> adding\n")


def test_cli_prints_the_graph_with_a_session_state(
    config: HirakuConfig, monkeypatch
) -> None:
    workflow = machine(config)
    first = workflow.run_start({})
    send(workflow, "give", first, number=1)
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    result = runner.invoke(
        main, ["workflow", "graph", "machine", "--session", first["session_id"]]
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("stateDiagram-v2\n  [*] --> ask\n")
    assert result.output.endswith("  class check current\n")
    shown = runner.invoke(main, ["session", "show", first["session_id"]])
    assert "State:    check" in shown.output


NAMED_PY = '''\
from __future__ import annotations

from pydantic import BaseModel

from hiraku.workflow import Instruction, Workflow, event, state


class Notes(BaseModel):
    notes: list[str] = []


class Named(Workflow):
    def start(self) -> Notes:
        return Notes()

    @state("ask", start=True)
    def asking(self, notes: Notes) -> Instruction:
        return Instruction("Ask.")

    @state("check")
    def checking(self, notes: Notes) -> Instruction:
        return Instruction("Check.")

    @state("done", final=True)
    def finishing(self, notes: Notes) -> Instruction:
        return Instruction("Done.", context={"notes": notes.notes})

    @event(source="ask", dest="check")
    def next_step(self, notes: Notes) -> None:
        """Go to the check."""

    @event("note", source="ask", dest="ask")
    def note_question(self, notes: Notes, text: str) -> None:
        """Note something about the question."""
        notes.notes.append(f"question: {text}")

    @event("note", source="check", dest="check")
    def note_answer(self, notes: Notes, text: str) -> None:
        """Note something about the answer."""
        notes.notes.append(f"answer: {text}")

    @event(source="check", dest="done")
    def finish(self, notes: Notes) -> None:
        """Finish."""


WORKFLOW = Named
'''


def described(response: dict) -> dict[str, str]:
    return {
        item["event"]: item["description"]
        for item in response["instruction"]["events"]["accepted"]
    }


def test_named_events_run_the_method_of_the_current_state(
    config: HirakuConfig,
) -> None:
    write_workflow(config, "named", NAMED_PY, "# Named\n")
    workflow = load_workflow("named", config=config).bind(config)
    first = workflow.run_start({})
    assert described(first) == {
        "next_step": "Go to the check.",
        "note": "Note something about the question.",
    }
    noted = send(workflow, "note", first, text="why?")
    assert noted["instruction"]["text"] == "Ask."
    checking = send(workflow, "next_step", noted)
    assert described(checking)["note"] == "Note something about the answer."
    checked = send(workflow, "note", checking, text="because")
    done = send(workflow, "finish", checked)
    assert done["instruction"]["context"] == {
        "notes": ["question: why?", "answer: because"]
    }
    assert [
        item.event for item in Session.load(first["session_id"], config=config).history
    ] == ["start", "note", "next_step", "note", "finish"]
    graph = workflow_graph(workflow)
    assert "  ask --> ask: note\n  check --> check: note\n" in graph


def test_named_events_need_disjoint_source_states(config: HirakuConfig) -> None:
    source = NAMED_PY.replace(
        '    @event("note", source="check", dest="check")\n',
        '    @event("note", source=["ask", "check"], dest="check")\n',
    )
    write_workflow(config, "named", source, "# Named\n")
    assert (
        "Event 'note' of Named is handled twice in state 'ask', by note_question "
        "and note_answer"
    ) in load_error(config, "named")
    source = NAMED_PY.replace(
        '    @event("note", source="ask"', '    @event("a-note", source="ask"'
    )
    write_workflow(config, "named", source, "# Named\n")
    assert "Event names use letters, digits, and '_'" in load_error(config, "named")


def test_instructions_narrow_named_events_by_method(config: HirakuConfig) -> None:
    narrowed = NAMED_PY.replace(
        '        return Instruction("Ask.")\n',
        '        return Instruction("Ask.", events=[self.note_question])\n',
    )
    write_workflow(config, "named", narrowed, "# Named\n")
    workflow = load_workflow("named", config=config).bind(config)
    assert accepted(workflow.run_start({})) == ["note"]
    wrong = narrowed.replace("events=[self.note_question]", "events=[self.note_answer]")
    write_workflow(config, "named", wrong, "# Named\n")
    response = load_workflow("named", config=config).bind(config).run_start({})
    assert response["status"] == "error"
    assert "State 'ask' does not accept note_answer" in response["message"]


GATED_PY = (
    MINIMAL
    + "class W(Workflow):\n"
    + "    def start(self) -> S:\n        return S()\n"
    + STATES.replace(
        "@event(source='go', dest='end')",
        "@event(source='go', dest='end', requires_approval=True)",
    ).replace(
        "-> Instruction: ...", "-> Instruction:\n        return Instruction('Go.')"
    )
    + "WORKFLOW = W\n"
)


def test_events_requiring_approval_run_only_when_approved(
    config: HirakuConfig,
) -> None:
    write_workflow(config, "gated", GATED_PY)
    workflow = load_workflow("gated", config=config).bind(config)
    graph = workflow_graph(workflow)
    assert "go --> end: stop (approval)" in graph
    first = workflow.run_start({})
    assert first["instruction"]["events"]["accepted"][0]["requires_approval"] is True
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    asked = send(workflow, "stop", first)
    assert asked["status"] == "approval_required"
    assert asked["approval"] == {"event": "stop", "description": "", "arguments": {}}
    assert asked["instruction_id"] == 1
    assert path.read_text() == before

    approved = workflow.run_event(first["session_id"], 1, "stop", {}, approved=True)
    assert approved["status"] == "accepted"
    assert approved["completed"] is True
    # Retrying the approved event returns its response without asking again.
    assert send(workflow, "stop", first) == approved


def test_cli_approves_events(config: HirakuConfig, monkeypatch) -> None:
    monkeypatch.chdir(config.root)
    write_workflow(config, "gated", GATED_PY)
    first = load_workflow("gated", config=config).bind(config).run_start({})
    command = ["workflow", "send-event", first["session_id"], "stop"]
    command += ["--instruction-id", "1"]
    asked = CliRunner().invoke(main, command)
    assert asked.exit_code == 0, asked.output
    assert json.loads(asked.output)["status"] == "approval_required"
    approved = CliRunner().invoke(main, [*command, "--approve"])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["status"] == "accepted"
