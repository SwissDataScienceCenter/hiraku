from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import (
    HirakuError,
    SessionNotFoundError,
    ValidationError,
    WorkflowError,
)
from hiraku.session import Session
from hiraku.workflow import (
    Completion,
    Instruction,
    PromptWorkflow,
    Workflow,
    handlers,
    list_workflows,
    load_workflow,
    load_workflows,
    session_model,
    workflow_description,
)

TOY_PY = '''\
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.workflow import Session, Workflow, tool

from .helpers import scale


class ToySession(Session):
    target: int
    total: int = 0


class Toy(Workflow):
    """Add numbers until a target is reached.

    Used to exercise the workflow runtime.
    """

    step: int = 1

    @tool
    def start(
        self, target: Annotated[int, Field(gt=0, description="Sum to reach.")] = 3
    ):
        """Start adding."""
        return self.ask(ToySession(target=target))

    @tool
    def add(self, session: ToySession, amount: int):
        """Add an amount."""
        if amount < 0:
            raise SubmissionRejected("amount must not be negative")
        session.total += scale(amount) * self.step
        if session.total >= session.target:
            return self.completion(session, {"total": session.total})
        return self.ask(session)

    @tool
    def wait(self, session: ToySession):
        """Wait for evidence."""
        session.total = -100
        raise NotReady("evidence is not there yet")

    @tool
    def boom(self, session: ToySession):
        """Fail unexpectedly."""
        session.total = -100
        raise RuntimeError("kaboom")

    @tool
    def hidden(self, session: ToySession):
        """Never offered."""
        return self.ask(session)

    @tool
    def peek(self, value: int) -> dict:
        """An ordinary tool."""
        return {"value": value}

    @tool("save")
    def save_total(self, session: ToySession):
        """Finish and report the total."""
        return self.completion(session, {"saved": session.total})

    def ask(self, session):
        return self.instruction(
            session,
            self.section("add"),
            context={"total": session.total},
            replies=[self.add, self.wait, self.boom],
        )


WORKFLOW = Toy
'''

TOY_MD = "# Toy\n\nAdd numbers.\n\n## Add\n\nAdd a number.\n"


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
    selected = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
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
    response = workflow.call("start", {"target": target})
    assert response["status"] == "accepted", response
    return response


def reply(workflow: Workflow, tool: str, response: dict, **arguments) -> dict:
    return workflow.call(
        tool,
        {
            "session_id": response["session_id"],
            "instruction_id": response["instruction_id"],
            **arguments,
        },
    )


def stored(config: HirakuConfig, session_id: str) -> dict:
    return json.loads((config.sessions_dir / f"{session_id}.json").read_text())


def tool_names(config: HirakuConfig) -> dict[str, Any]:
    return {tool.name: tool for tool in Workflow.tools(lambda: config)}


def test_handlers_schemas_and_relative_imports(config: HirakuConfig) -> None:
    workflow = toy(config)
    assert workflow_description(workflow).startswith("Add numbers until a target")
    registered = handlers(type(workflow))
    kinds = {name: item.kind for name, item in registered.items()}
    assert kinds["start"] == "start"
    assert kinds["add"] == "continuation"
    assert kinds["peek"] == "tool"
    tools = tool_names(config)
    start = tools["toy"].parameters
    assert start["properties"]["target"]["default"] == 3
    assert start["properties"]["target"]["description"] == "Sum to reach."
    assert start["properties"]["target"]["exclusiveMinimum"] == 0
    assert tools["toy"].description.startswith("Start a toy workflow session.")
    assert "toy_peek" in tools
    assert not any(name.startswith("toy_add") for name in tools)


def test_handlers_stay_directly_callable(config: HirakuConfig) -> None:
    workflow = toy(config)
    model = session_model(type(workflow))
    assert model is not None
    response = workflow.add(model(target=5), amount=2)
    assert isinstance(response, Instruction)
    assert response.replies == ("add", "wait", "boom")
    assert response.context == {"total": 2}
    assert isinstance(workflow.add(model(target=1), amount=2), Completion)
    assert workflow.peek(4) == {"value": 4}


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("x = 1\n", "must export WORKFLOW"),
        (
            "from hiraku.workflow import Workflow, tool\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def other(self) -> None: ...\n"
            "WORKFLOW = W\n",
            "must define a @tool start",
        ),
        (
            "from hiraku.workflow import Session, Workflow, tool\n"
            "class S(Session):\n    pass\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self, session: S) -> None: ...\n"
            "WORKFLOW = W\n",
            "cannot take one",
        ),
        (
            "from hiraku.workflow import Session, Workflow, tool\n"
            "class S(Session):\n    pass\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self) -> None: ...\n"
            "    @tool\n"
            "    def two(self, a: S, b: S) -> None: ...\n"
            "WORKFLOW = W\n",
            "more than one session",
        ),
        (
            "from hiraku.workflow import Session, Workflow, tool\n"
            "class S(Session):\n    status: str = 'mine'\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self) -> None: ...\n"
            "WORKFLOW = W\n",
            "redefines reserved Session names: status",
        ),
        (
            "from hiraku.workflow import Workflow, tool\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self, instruction_id: int) -> None: ...\n"
            "WORKFLOW = W\n",
            "reserved parameter",
        ),
        (
            "from hiraku.workflow import Workflow, tool\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self, *values: int) -> None: ...\n"
            "WORKFLOW = W\n",
            "cannot use",
        ),
        (
            "from hiraku.workflow import Workflow, tool\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self, untyped) -> None: ...\n"
            "WORKFLOW = W\n",
            "needs a type annotation",
        ),
        (
            "from hiraku.workflow import Workflow, tool\n"
            "class W(Workflow):\n"
            "    @tool\n"
            "    def start(self) -> None: ...\n"
            "    @tool\n"
            "    def section(self) -> None: ...\n"
            "WORKFLOW = W\n",
            "shadows a Workflow attribute",
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
    tools = tool_names(config)
    assert "Workflow bad is unavailable" in caplog.text
    assert message in caplog.text
    assert "toy" in tools
    assert not any(name == "bad" or name.startswith("bad_") for name in tools)
    assert message in load_error(config, "bad")


def test_workflow_ids_cannot_make_tool_names_ambiguous(config: HirakuConfig) -> None:
    simple = (
        "from hiraku.workflow import Workflow, tool\n"
        "class W(Workflow):\n"
        "    @tool\n"
        "    def start(self) -> None: ...\n"
        "{extra}"
        "WORKFLOW = W\n"
    )
    write_workflow(config, "bad.name", simple.format(extra=""))
    write_workflow(config, "x_y", simple.format(extra=""))
    write_workflow(
        config,
        "x",
        simple.format(extra="    @tool\n    def y_start(self) -> None: ...\n"),
    )
    tools = tool_names(config)
    for identifier in ("bad.name", "x_y"):
        assert "Invalid workflow ID" in load_error(config, identifier)
        assert identifier not in tools
    assert {"x", "x_y_start"} <= set(tools)
    with pytest.raises(WorkflowError, match="Unknown workflow tool"):
        toy(config).call("missing", {})


def test_prompts_are_snapshotted_with_the_code(config: HirakuConfig) -> None:
    workflow = toy(config)
    (config.workflows_dir / "toy" / "workflow.md").write_text(
        document("toy", "# Changed\n\n## Add\n\nNew\n")
    )
    assert workflow.section("add") == "## Add\n\nAdd a number."
    assert toy(config).section("add") == "## Add\n\nNew"


def test_start_returns_orientation_and_explicit_replies(config: HirakuConfig) -> None:
    response = started(toy(config))
    assert response["orientation"] == "# Toy\n\nAdd numbers."
    assert "workflow_reply" in response["guide"]
    assert response["instruction_id"] == 1
    instruction = response["instruction"]
    assert instruction["text"] == "## Add\n\nAdd a number."
    assert instruction["context"] == {"total": 0}
    assert instruction["requires_user_response"] is False
    assert instruction["reply_with"] == "workflow_reply"
    assert [item["reply"] for item in instruction["replies"]] == ["add", "wait", "boom"]
    assert instruction["replies"][0]["description"] == "Add an amount."
    assert instruction["replies"][0]["arguments"] == {
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
    }
    saved = stored(config, response["session_id"])
    assert saved["target"] == 3
    assert saved["total"] == 0
    assert saved["workflow"] == "toy"
    assert saved["instruction_id"] == 1
    assert saved["replies"] == ["add", "wait", "boom"]
    assert (config.sessions_dir / ".gitignore").read_text() == "*\n"


def test_start_validation_errors_create_no_session(config: HirakuConfig) -> None:
    response = toy(config).call("start", {"target": 0})
    assert response["status"] == "rejected"
    assert "target" in response["message"]
    assert Session.list_all(config=config) == []


def test_correlation_table(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=5)
    accepted = reply(workflow, "add", first, amount=2)
    assert accepted["status"] == "accepted"
    assert accepted["instruction_id"] == 2
    assert accepted["instruction"]["context"] == {"total": 2}

    retry = reply(workflow, "add", first, amount=2)
    assert retry == accepted

    conflict = reply(workflow, "add", first, amount=1)
    assert conflict["status"] == "stale"
    assert "already answered" in conflict["message"]
    assert conflict["instruction"]["context"] == {"total": 2}

    future = workflow.call(
        "add", {"session_id": first["session_id"], "instruction_id": 9, "amount": 1}
    )
    assert future["status"] == "stale"
    assert "pending instruction is 2" in future["message"]

    not_offered = reply(workflow, "hidden", accepted)
    assert not_offered["status"] == "rejected"
    assert "not a reply to the pending instruction" in not_offered["message"]

    second = reply(workflow, "add", accepted, amount=1)
    stale = reply(workflow, "add", first, amount=5)
    assert second["instruction_id"] == 3
    assert stale["status"] == "stale"
    assert stale["instruction_id"] == 3
    assert stored(config, first["session_id"])["total"] == 3


def test_replies_find_the_active_session(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=5)
    response = workflow.call("add", {"instruction_id": 1, "amount": 2})
    assert response["status"] == "accepted"
    assert response["session_id"] == first["session_id"]

    second = started(workflow)
    several = workflow.call("add", {"instruction_id": 2, "amount": 1})
    assert several["status"] == "rejected"
    assert "pass session_id" in several["message"]

    Session.load(first["session_id"], config=config).cancel()
    Session.load(second["session_id"], config=config).cancel()
    none = workflow.call("add", {"instruction_id": 1, "amount": 1})
    assert none["status"] == "rejected"
    assert "start one with the toy tool" in none["message"]


def test_completion_and_retried_completion(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=2)
    done = reply(workflow, "add", first, amount=2)
    assert done == {
        "status": "accepted",
        "session_id": first["session_id"],
        "completed": True,
        "result": {"total": 2},
    }
    assert reply(workflow, "add", first, amount=2) == done
    after = workflow.call(
        "add", {"session_id": first["session_id"], "instruction_id": 2, "amount": 1}
    )
    assert after["status"] == "rejected"
    assert "completed" in after["message"]
    shown = Session.load(first["session_id"], config=config).show()
    assert shown["status"] == "completed"


def test_rejections_and_not_ready_save_nothing(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    schema = reply(workflow, "add", first, amount="many")
    assert schema["status"] == "rejected"
    assert "amount" in schema["message"]
    rejected = reply(workflow, "add", first, amount=-1)
    assert rejected["status"] == "rejected"
    assert rejected["message"] == "amount must not be negative"
    not_ready = reply(workflow, "wait", first)
    assert not_ready["status"] == "not_ready"
    assert not_ready["instruction_id"] == 1
    assert not_ready["instruction"]["text"] == "## Add\n\nAdd a number."
    error = reply(workflow, "boom", first)
    assert error["status"] == "error"
    assert "RuntimeError: kaboom" in error["message"]
    assert error["instruction_id"] == 1
    assert path.read_text() == before
    assert reply(workflow, "add", first, amount=1)["status"] == "accepted"


def test_handlers_cannot_change_runtime_fields(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        "        session.total += scale(amount) * self.step\n",
        "        session.total += scale(amount) * self.step\n"
        "        session.status = 'cancelled'\n"
        "        session.instruction_id = 99\n",
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    workflow = toy(config)
    first = started(workflow, target=5)
    response = reply(workflow, "add", first, amount=1)
    assert response["status"] == "accepted"
    assert response["instruction_id"] == 2
    assert stored(config, first["session_id"])["status"] == "active"


def test_invalid_correlation_arguments(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    with pytest.raises(ValidationError, match="Invalid session ID"):
        workflow.call("add", {"session_id": "../x", "instruction_id": 1})
    with pytest.raises(ValidationError, match="instruction_id"):
        workflow.call("add", {"session_id": first["session_id"], "amount": 1})
    with pytest.raises(SessionNotFoundError):
        workflow.call("add", {"session_id": "missing", "instruction_id": 1})


def test_ordinary_tools(config: HirakuConfig) -> None:
    workflow = toy(config)
    assert workflow.call("peek", {"value": 3}) == {
        "status": "ok",
        "result": {"value": 3},
    }
    assert workflow.call("peek", {})["status"] == "rejected"


def test_restart_recovery_continues_from_disk(config: HirakuConfig) -> None:
    first = started(toy(config), target=4)
    restarted = load_workflow("toy", config=HirakuConfig.discover(config.root))
    shown = Session.load(first["session_id"], config=config).show(restarted)
    assert shown["response"]["instruction"] == first["instruction"]
    assert shown["orientation"] == first["orientation"]
    assert reply(restarted.bind(config), "add", first, amount=1)["status"] == (
        "accepted"
    )


def test_concurrent_replies_apply_once(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=10)
    barrier = threading.Barrier(4)
    results: list[dict] = []

    def worker() -> None:
        barrier.wait()
        results.append(reply(workflow, "add", first, amount=1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item["status"] for item in results} == {"accepted"}
    assert {item["instruction_id"] for item in results} == {2}
    assert stored(config, first["session_id"])["total"] == 1


def test_failed_save_keeps_previous_state(config: HirakuConfig, monkeypatch) -> None:
    workflow = toy(config)
    first = started(workflow)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.session.atomic_write", fail)
    with pytest.raises(ValidationError, match="disk full"):
        reply(workflow, "add", first, amount=1)
    monkeypatch.undo()
    assert path.read_text() == before
    assert reply(workflow, "add", first, amount=1)["status"] == "accepted"


def test_incompatible_sessions_are_rejected_but_inspectable(
    config: HirakuConfig,
) -> None:
    first = started(toy(config))
    path = config.workflows_dir / "toy" / "workflow.py"
    path.write_text(
        TOY_PY.replace("    total: int = 0\n", "    total: int = 0\n    new: str\n")
    )
    workflow = toy(config)
    rejected = reply(workflow, "add", first, amount=1)
    assert rejected["status"] == "rejected"
    assert "no longer matches" in rejected["message"]
    shown = Session.load(first["session_id"], config=config).show(workflow)
    assert "new" in shown["compatibility"]

    path.write_text(TOY_PY.replace("    def wait(", "    def waiting("))
    shown = Session.load(first["session_id"], config=config).show(toy(config))
    assert "pending replies wait" in shown["compatibility"]


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


def test_cancelled_sessions_reject_replies(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow)
    Session.load(first["session_id"], config=config).cancel()
    response = reply(workflow, "add", first, amount=1)
    assert response["status"] == "rejected"
    assert "cancelled" in response["message"]


def test_cannot_cancel_completed_session(config: HirakuConfig) -> None:
    workflow = toy(config)
    first = started(workflow, target=1)
    reply(workflow, "add", first, amount=1)
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
        "autodataset",
        "autoresearch-python",
        "guided-research",
        "hiraku-agent",
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
        main, ["workflow", "call", "toy", "--arguments-file", str(arguments)]
    )
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["status"] == "accepted"
    session_id = response["session_id"]
    assert response == {
        **Session.load(session_id, config=config).show()["response"],
        "status": "accepted",
        "guide": response["guide"],
        "orientation": "# Toy\n\nAdd numbers.",
    }

    follow = json.dumps(
        {"reply": "add", "instruction_id": 1, "arguments": {"amount": 1}}
    )
    result = runner.invoke(
        main,
        ["workflow", "call", "workflow_reply", "--arguments-file", "-"],
        input=follow,
    )
    assert json.loads(result.output)["status"] == "accepted"

    listing = runner.invoke(main, ["workflow", "list", "--json"])
    assert {item["id"] for item in json.loads(listing.output)} >= {"toy"}
    shown = runner.invoke(main, ["session", "show", session_id])
    assert "Pending instruction 2" in shown.output
    assert "Replies: add, wait, boom" in shown.output
    sessions = json.loads(runner.invoke(main, ["session", "list", "--json"]).output)
    assert sessions[0]["session_id"] == session_id
    cancelled = runner.invoke(main, ["session", "cancel", session_id])
    assert "cancelled" in cancelled.output
    unknown = runner.invoke(main, ["workflow", "call", "nope_start"])
    assert unknown.exit_code != 0
    assert "Unknown workflow" in unknown.output


def test_tool_names_can_differ_from_method_names(config: HirakuConfig) -> None:
    workflow = toy(config)
    assert handlers(type(workflow))["save"].method == "save_total"
    model = session_model(type(workflow))
    assert model is not None
    instruction = workflow.instruction(
        model(target=3), "Done?", replies=[workflow.save_total, workflow.add]
    )
    assert instruction.replies == ("save", "add")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "    @tool\n    def save(self) -> None: ...\n",
            "shadows a Workflow attribute; rename the method and keep the name "
            'with @tool("save")',
        ),
        (
            "    @tool\n    def call(self) -> None: ...\n",
            "shadows a Workflow attribute",
        ),
        (
            "    @tool('peek')\n    def a(self) -> None: ...\n"
            "    @tool('peek')\n    def b(self) -> None: ...\n",
            "Tool name 'peek' is used by both W.a and W.b",
        ),
        (
            "    @tool('bad name')\n    def a(self) -> None: ...\n",
            "Invalid tool name 'bad name'",
        ),
        ("    LIMIT = 3\n", "non-annotated attribute"),
    ],
)
def test_invalid_method_and_tool_names(
    config: HirakuConfig, body: str, message: str
) -> None:
    source = (
        "from hiraku.workflow import Workflow, tool\n"
        "class W(Workflow):\n"
        "    @tool\n"
        "    def start(self) -> None: ...\n"
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
    assert reply(workflow, "add", first, amount=3)["instruction"]["context"] == {
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
    assert second.section("add") == "## Add\n\nAdd a number."
    assert (
        not (config.workflows_dir / "toy" / "workflow.md")
        .read_text()
        .startswith("# Changed")
    )


def test_markdown_only_workflows_send_the_whole_prompt(config: HirakuConfig) -> None:
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
    assert set(handlers(type(loaded))) == {"start", "complete"}

    workflow = loaded.bind(config)
    first = workflow.call("start", {"task": "sep10", "iterations": "3"})
    assert first["status"] == "accepted"
    assert first["orientation"] == ""
    assert first["instruction"]["text"].startswith(
        "# Loop\n\nRun 3 experiments on sep10 (quick: false)."
    )
    assert first["instruction"]["context"] == {
        "task": "sep10",
        "iterations": 3,
        "quick": False,
    }
    assert [item["reply"] for item in first["instruction"]["replies"]] == ["complete"]
    assert reply(workflow, "complete", first, summary=" ")["status"] == "rejected"
    done = reply(workflow, "complete", first, summary="Tried three ideas")
    assert done["result"] == {"summary": "Tried three ideas"}
    stored = Session.load(first["session_id"], config=config)
    assert stored.show()["data"] == {}
    assert [(item.reply, item.instruction_id) for item in stored.history] == [
        ("start", 0),
        ("complete", 1),
    ]

    missing = workflow.call("start", {})
    assert missing["status"] == "rejected"
    assert "task" in missing["message"]
    unknown = workflow.call("start", {"task": "x", "other": 1})
    assert unknown["status"] == "rejected"

    (directory / "workflow.md").write_text("---\nid: loop\n---\nUse {{nope}}.\n")
    assert "placeholders without declared arguments: nope" in load_error(config, "loop")
    (directory / "workflow.md").write_text(body)
    assert "front matter" in load_error(config, "loop")


def test_workflows_and_sessions_provide_their_tools(config: HirakuConfig) -> None:
    tools = {
        tool.name: tool for tool in Workflow.tools(lambda: config, {"toy": toy(config)})
    }
    assert set(tools) == {"workflow_list", "workflow_reply", "toy", "toy_peek"}
    first = tools["toy"].fn(target=2)
    assert first["status"] == "accepted"
    done = tools["workflow_reply"].fn(
        reply="add", instruction_id=1, arguments={"amount": 2}
    )
    assert done["result"] == {"total": 2}
    assert tools["workflow_list"].fn()[0]["id"] == "toy"

    sessions = {tool.name: tool for tool in Session.tools(lambda: config)}
    assert set(sessions) == {"session_list", "session_show", "session_cancel"}
    shown = sessions["session_show"].fn(session_id=first["session_id"])
    assert shown["status"] == "completed"
    assert shown["orientation"] == "# Toy\n\nAdd numbers."
