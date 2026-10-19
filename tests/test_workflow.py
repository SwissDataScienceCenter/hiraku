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
    WorkflowError,
)
from hiraku.markdown import Markdown
from hiraku.session import Session
from hiraku.workflow import (
    Instruction,
    PromptWorkflow,
    States,
    Workflow,
    discover_workflows,
    event_methods,
    list_workflows,
    load_workflow,
    load_workflows,
    start_model,
    state_model,
    workflow_description,
    workflow_graph,
    workflow_states,
)

TOY_PY = '''\
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from hiraku.errors import NotReady, EventRejected
from hiraku.workflow import Instruction, Workflow, event

from .helpers import scale


class ToyState(BaseModel):
    target: int
    total: int = 0


class Toy(Workflow):
    """Add numbers until a target is reached.

    Used to exercise the workflow runtime.
    """

    step: int = 1

    def start(
        self, target: Annotated[int, Field(gt=0, description="Sum to reach.")] = 3
    ) -> ToyState:
        """Start adding."""
        return ToyState(target=target)

    def next(self, state: ToyState) -> Instruction:
        if state.total >= state.target:
            return Instruction(
                self.markdown.section("## Done"),
                context={"total": state.total},
                final=True,
            )
        return Instruction(
            self.markdown.section("## Add"),
            context={"total": state.total},
            events=[self.add, self.wait, self.boom],
        )

    @event
    def add(self, state: ToyState, amount: int) -> None:
        """Add an amount."""
        if amount < 0:
            raise EventRejected("amount must not be negative")
        state.total += scale(amount) * self.step

    @event
    def wait(self, state: ToyState) -> None:
        """Wait for evidence."""
        state.total = -100
        raise NotReady("evidence is not there yet")

    @event
    def boom(self, state: ToyState) -> None:
        """Fail unexpectedly."""
        state.total = -100
        raise RuntimeError("kaboom")

    @event
    def hidden(self, state: ToyState) -> None:
        """Never offered."""


WORKFLOW = Toy
'''

TOY_MD = (
    "# Toy\n\nAdd numbers.\n\n## Add\n\nAdd a number.\n\n## Done\n\nReport the total.\n"
)

# A minimal valid workflow class, for sources that test one invalid part.
MINIMAL = (
    "from pydantic import BaseModel\n"
    "from hiraku.workflow import Instruction, Workflow, event\n"
    "class S(BaseModel):\n    pass\n"
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
    assert set(event_methods(type(workflow))) == {"add", "boom", "hidden", "wait"}


def test_start_next_and_events_stay_directly_callable(config: HirakuConfig) -> None:
    workflow = toy(config)
    state = workflow.start(target=5)
    assert type(state) is state_model(type(workflow))
    assert workflow.add(state, amount=2) is None
    assert state.total == 2
    instruction = workflow.next(state)
    assert isinstance(instruction, Instruction)
    assert instruction.events == [workflow.add, workflow.wait, workflow.boom]
    assert instruction.context == {"total": 2}
    workflow.add(state, amount=3)
    assert workflow.next(state).final is True


@pytest.mark.parametrize(
    ("next_body", "message"),
    [
        (
            "        return Instruction('Ask', events=[self.next])\n",
            "must be @event methods",
        ),
        (
            "        return Instruction('Ask', events=[self.add, self.add])\n",
            "same event more than once",
        ),
        ("        return Instruction('Ask')\n", "at least one event, or be final"),
        (
            "        return Instruction('Ask', events=[self.add], final=True)\n",
            "final instruction accepts no events",
        ),
        (
            "        return Instruction(\n"
            "            'Ask', final=True, requires_user_response=True\n"
            "        )\n",
            "requires no user response",
        ),
        (
            "        return Instruction(' ', events=[self.add])\n",
            "must not be empty",
        ),
        (
            "        return Instruction('Ask', context=object(), events=[self.add])\n",
            "must be JSON-compatible",
        ),
        ("        return 'Ask'\n", "it must return an Instruction"),
        (
            "        state.total = 1\n"
            "        return Instruction('Ask', events=[self.add])\n",
            "next of workflow 'toy' changed the state",
        ),
    ],
)
def test_invalid_instructions_are_errors_and_save_nothing(
    config: HirakuConfig, next_body: str, message: str
) -> None:
    start = TOY_PY.index("    def next(")
    end = TOY_PY.index("    @event\n    def add(")
    source = (
        TOY_PY[:start]
        + "    def next(self, state: ToyState) -> Instruction:\n"
        + next_body
        + "\n"
        + TOY_PY[end:]
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    response = toy(config).run_start({"target": 3})
    assert response["status"] == "error"
    assert message in response["message"]
    assert Session.list_all(config=config) == []


def test_events_must_return_nothing(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        "        state.total += scale(amount) * self.step\n",
        "        state.total += scale(amount) * self.step\n        return state\n",
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    workflow = toy(config)
    first = started(workflow)
    response = send(workflow, "add", first, amount=1)
    assert response["status"] == "error"
    assert "events change the state and return nothing" in response["message"]
    assert stored(config, first["session_id"])["data"] == {"target": 3, "total": 0}


def test_start_must_return_the_state(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        "        return ToyState(target=target)", "        return {'target': target}"
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    response = toy(config).run_start({"target": 3})
    assert response["status"] == "error"
    assert "start must return a ToyState" in response["message"]


NEXT = "    def next(self, state: S) -> Instruction: ...\n"


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
            "    def start(self): ...\n" + NEXT + "WORKFLOW = W\n",
            "must declare its state as the return type",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> int: ...\n" + NEXT + "WORKFLOW = W\n",
            "must declare its state as the return type",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            "WORKFLOW = W\n",
            "must define next(self, state)",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n"
            "    def next(self, state: S, extra: int) -> Instruction: ...\n"
            "WORKFLOW = W\n",
            "W.next must take only the state",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self) -> S: ...\n" + NEXT + "    @event\n"
            "    def other(self, value: int) -> None: ...\n"
            "WORKFLOW = W\n",
            "must take the state as its first parameter, annotated with S",
        ),
        (
            MINIMAL + "class T(BaseModel):\n    pass\n"
            "class W(Workflow):\n"
            "    def start(self) -> S: ...\n" + NEXT + "    @event\n"
            "    def other(self, state: T) -> None: ...\n"
            "WORKFLOW = W\n",
            "must take the state as its first parameter, annotated with S",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self, *values: int) -> S: ...\n" + NEXT + "WORKFLOW = W\n",
            "cannot use",
        ),
        (
            MINIMAL + "class W(Workflow):\n"
            "    def start(self, untyped) -> S: ...\n" + NEXT + "WORKFLOW = W\n",
            "needs a type annotation",
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
        "    def start(self) -> S: ...\n" + NEXT + "WORKFLOW = W\n"
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
    assert "stored state no longer matches" in rejected["message"]
    shown = Session.load(first["session_id"], config=config).show(workflow)
    assert "new" in shown["compatibility"]

    path.write_text(TOY_PY.replace("    def wait(", "    def waiting("))
    shown = Session.load(first["session_id"], config=config).show(toy(config))
    assert "pending events wait" in shown["compatibility"]


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
            "    @event\n    def save(self, state: S) -> None: ...\n",
            "@event W.save is named like a Workflow method",
        ),
        (
            "    @event\n    def run_event(self, state: S) -> None: ...\n",
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
        "    def start(self) -> S: ...\n"
        "    def next(self, state: S) -> Instruction: ...\n"
        f"{body}"
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
    assert reloaded.markdown.section("## Review") == document.markdown.section(
        "## Review"
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


# States: events with source and dest -------------------------------------------

STATED_PY = '''\
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from hiraku.workflow import Instruction, Workflow, event


class State(BaseModel):
    state: Literal["ask", "check", "done"] = "ask"
    value: int = 0


class Stated(Workflow):
    def start(self) -> State:
        return State()

    def next(self, state: State) -> Instruction:
        if state.state == "done":
            return Instruction("Done.", context={"value": state.value}, final=True)
        return Instruction(f"State {state.state}.")

    @event(source="ask", dest="check")
    def give(self, state: State, value: int) -> None:
        """Give a value."""
        state.value = value
        state.state = "check"

    @event(source="check", dest=["ask", "done"])
    def judge(self, state: State, ok: bool) -> None:
        """Accept the value, or ask again."""
        state.state = "done" if ok else "ask"

    @event(source=["ask", "check"], dest="done")
    def stop(self, state: State) -> None:
        """Stop."""
        state.state = "done"


WORKFLOW = Stated
'''


def stated(config: HirakuConfig, source: str = STATED_PY) -> Workflow:
    write_workflow(config, "stated", source, "# Stated\n")
    return load_workflow("stated", config=config).bind(config)


def accepted(response: dict) -> list[str]:
    return [item["event"] for item in response["instruction"]["events"]["accepted"]]


def test_states_decide_which_events_an_instruction_accepts(
    config: HirakuConfig,
) -> None:
    workflow = stated(config)
    first = workflow.run_start({})
    assert first["status"] == "accepted"
    assert accepted(first) == ["give", "stop"]
    early = send(workflow, "judge", first, ok=True)
    assert early["status"] == "rejected"
    assert "not an event the current instruction accepts" in early["message"]

    checking = send(workflow, "give", first, value=3)
    assert checking["instruction"]["text"] == "State check."
    assert accepted(checking) == ["judge", "stop"]
    again = send(workflow, "judge", checking, ok=False)
    assert accepted(again) == ["give", "stop"]
    checking = send(workflow, "give", again, value=4)
    done = send(workflow, "judge", checking, ok=True)
    assert done["completed"] is True
    assert done["instruction"]["context"] == {"value": 4}
    stored_session = Session.load(first["session_id"], config=config)
    assert stored_session.data == {"state": "done", "value": 4}


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '        state.value = value\n        state.state = "check"\n',
            '        state.value = value\n        state.state = "done"\n',
            "moved to state 'done'; it declares dest check",
        ),
        (
            '        return Instruction(f"State {state.state}.")\n',
            '        return Instruction("Ask.", events=[self.judge])\n',
            "State 'ask' does not accept judge; it accepts give, stop",
        ),
        (
            '        return Instruction(f"State {state.state}.")\n',
            '        return Instruction("Ask.", final=True)\n',
            "only a state without events has a final instruction",
        ),
    ],
)
def test_state_violations_are_errors_and_save_nothing(
    config: HirakuConfig, old: str, new: str, message: str
) -> None:
    workflow = stated(config, STATED_PY.replace(old, new))
    first = workflow.run_start({})
    if first["status"] == "error":
        assert message in first["message"]
        assert Session.list_all(config=config) == []
        return
    response = send(workflow, "give", first, value=1)
    assert response["status"] == "error"
    assert message in response["message"]
    assert stored(config, first["session_id"])["data"] == {"state": "ask", "value": 0}


def test_instructions_may_narrow_the_events_of_a_state(config: HirakuConfig) -> None:
    workflow = stated(
        config,
        STATED_PY.replace(
            '        return Instruction(f"State {state.state}.")\n',
            '        return Instruction("Only stop.", events=[self.stop])\n',
        ),
    )
    assert accepted(workflow.run_start({})) == ["stop"]


def test_start_must_return_the_start_state(config: HirakuConfig) -> None:
    workflow = stated(
        config,
        STATED_PY.replace(
            "        return State()\n", '        return State(state="check")\n'
        ),
    )
    response = workflow.run_start({})
    assert response["status"] == "error"
    assert "start state 'ask'" in response["message"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '    @event(source=["ask", "check"], dest="done")\n',
            "    @event\n",
            "Declare source and dest for every event of Stated or for none; "
            "missing for stop",
        ),
        (
            '    state: Literal["ask", "check", "done"] = "ask"\n',
            '    state: str = "ask"\n',
            "needs a state field listing them",
        ),
        (
            '    state: Literal["ask", "check", "done"] = "ask"\n',
            '    state: Literal["ask", "check", "done"]\n',
            "State.state needs a default, the start state",
        ),
        (
            '    @event(source="ask", dest="check")\n',
            '    @event(source="ask", dest=["check", "nowhere"])\n',
            "names unknown states nowhere",
        ),
        (
            '    state: Literal["ask", "check", "done"] = "ask"\n',
            '    state: Literal["ask", "check", "done", "orphan"] = "ask"\n',
            "cannot be reached from 'ask': orphan",
        ),
        (
            '    state: Literal["ask", "check", "done"] = "ask"\n',
            '    state: Literal["ask", "check", "done", "bad-name"] = "ask"\n',
            "State names use letters, digits, and '_': bad-name",
        ),
    ],
)
def test_inconsistent_states_fail_to_load(
    config: HirakuConfig, old: str, new: str, message: str
) -> None:
    write_workflow(config, "stated", STATED_PY.replace(old, new), "# Stated\n")
    assert message in load_error(config, "stated")


def test_states_are_drawn_as_a_mermaid_state_diagram(config: HirakuConfig) -> None:
    workflow = stated(config)
    assert workflow_states(type(workflow)) == States(
        names=("ask", "check", "done"), start="ask", final=("done",)
    )
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
    assert workflow_states(type(toy(config))) is None
    with pytest.raises(WorkflowError, match="has no states to draw"):
        workflow_graph(toy(config))


def test_cli_prints_the_graph_with_a_session_state(
    config: HirakuConfig, monkeypatch
) -> None:
    workflow = stated(config)
    first = workflow.run_start({})
    send(workflow, "give", first, value=1)
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    result = runner.invoke(
        main, ["workflow", "graph", "stated", "--session", first["session_id"]]
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("stateDiagram-v2\n  [*] --> ask\n")
    assert result.output.endswith("  class check current\n")
    unstated = runner.invoke(main, ["workflow", "graph", "toy"])
    assert unstated.exit_code != 0
    assert "has no states to draw" in unstated.output
