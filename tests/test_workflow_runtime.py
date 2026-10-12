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
    SessionNotFoundError,
    ValidationError,
    WorkflowError,
)
from hiraku.workflow import Completion, Instruction, PromptWorkflow, load_workflow
from hiraku.workflow.core import handlers, session_model
from hiraku.workflow.service import WorkflowService, workflow_description

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
            self.prompt("#add"),
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
    selected.set_mcp_workflows(None)
    return selected


def load_error(config: HirakuConfig, workflow_id: str) -> str:
    with pytest.raises(HirakuError) as info:
        load_workflow(workflow_id, config=config)
    return str(info.value)


def started(service: WorkflowService, target: int = 3) -> dict:
    response = service.call("toy_start", {"target": target})
    assert response["status"] == "accepted", response
    return response


def reply(service: WorkflowService, tool: str, response: dict, **arguments) -> dict:
    return service.call(
        tool,
        {
            "session_id": response["session_id"],
            "instruction_id": response["instruction_id"],
            **arguments,
        },
    )


def test_discovery_schemas_and_relative_imports(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    workflow = service.workflow("toy")
    definitions = {item["name"]: item for item in service.tool_definitions()}
    assert set(definitions) >= {
        "toy_start",
        "toy_add",
        "toy_peek",
        "autoresearch_start",
        "autodataset_start",
    }
    assert workflow_description(workflow).startswith("Add numbers until a target")
    kinds = {name: item.kind for name, item in handlers(type(workflow)).items()}
    assert kinds["start"] == "start"
    assert kinds["add"] == "continuation"
    assert kinds["peek"] == "tool"
    start = definitions["toy_start"]["input_schema"]
    assert start["properties"]["target"]["default"] == 3
    assert start["properties"]["target"]["description"] == "Sum to reach."
    add = definitions["toy_add"]["input_schema"]
    assert add["required"] == ["session_id", "instruction_id", "amount"]
    assert "Reply to a pending toy instruction" in definitions["toy_add"]["description"]


def test_handlers_stay_directly_callable(config: HirakuConfig) -> None:
    workflow = load_workflow("toy", config=config).bind(config)
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
            "    def prompt(self) -> None: ...\n"
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
    service = WorkflowService(config)
    assert "Workflow bad is unavailable" in caplog.text
    assert message in caplog.text
    assert "bad" not in service.workflows
    assert "toy" in service.workflows
    assert not any(
        item["name"].startswith("bad_") for item in service.tool_definitions()
    )
    with pytest.raises(HirakuError) as info:
        service.activate("bad")
    assert message in str(info.value)


def test_activation_retries_a_fixed_workflow(config: HirakuConfig) -> None:
    write_workflow(config, "late", "raise RuntimeError('not yet')\n")
    service = WorkflowService(config)
    with pytest.raises(WorkflowError, match="not yet"):
        service.activate("late")
    write_workflow(config, "late", TOY_PY, TOY_MD)
    (config.workflows_dir / "late" / "helpers.py").write_text(
        "def scale(value):\n    return value\n"
    )
    service.activate("late")
    assert "late_start" in {item["name"] for item in service.tool_definitions()}


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
    service = WorkflowService(config)
    for identifier in ("bad.name", "x_y"):
        assert "Invalid workflow ID" in load_error(config, identifier)
        assert identifier not in service.workflows
    assert service.tool("x_y_start")[1].name == "y_start"
    with pytest.raises(WorkflowError, match="Unknown workflow tool"):
        service.tool("x_missing")


def test_prompts_are_snapshotted_with_the_code(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    (config.workflows_dir / "toy" / "workflow.md").write_text(
        document("toy", "# Changed\n\n## Add\n\nNew\n")
    )
    assert service.workflow("toy").prompt("#add") == "## Add\n\nAdd a number."
    assert WorkflowService(config).workflow("toy").prompt("#add") == "## Add\n\nNew"


def test_start_returns_orientation_and_explicit_replies(config: HirakuConfig) -> None:
    response = started(WorkflowService(config))
    assert response["orientation"] == "# Toy\n\nAdd numbers."
    assert "reply tools" in response["guide"]
    assert response["instruction_id"] == 1
    instruction = response["instruction"]
    assert instruction["text"] == "## Add\n\nAdd a number."
    assert instruction["context"] == {"total": 0}
    assert instruction["requires_user_response"] is False
    assert [item["tool"] for item in instruction["replies"]] == [
        "toy_add",
        "toy_wait",
        "toy_boom",
    ]
    assert instruction["replies"][0]["arguments"] == {
        "session_id": response["session_id"],
        "instruction_id": 1,
    }
    assert instruction["replies"][0]["description"] == "Add an amount."
    stored = json.loads(
        (config.sessions_dir / f"{response['session_id']}.json").read_text()
    )
    assert stored["data"] == {"target": 3, "total": 0}
    assert (config.sessions_dir / ".gitignore").read_text() == "*\n"


def test_start_validation_errors_create_no_session(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    response = service.call("toy_start", {"target": 0})
    assert response["status"] == "rejected"
    assert "target" in response["message"]
    assert service.list_sessions() == []


def test_correlation_table(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service, target=5)
    accepted = reply(service, "toy_add", first, amount=2)
    assert accepted["status"] == "accepted"
    assert accepted["instruction_id"] == 2
    assert accepted["instruction"]["context"] == {"total": 2}

    duplicate = reply(service, "toy_add", first, amount=2)
    assert duplicate["status"] == "duplicate"
    assert duplicate["instruction_id"] == 2

    conflict = reply(service, "toy_add", first, amount=1)
    assert conflict["status"] == "conflict"
    assert "not applied" in conflict["message"]
    assert conflict["instruction"]["context"] == {"total": 2}

    future = service.call(
        "toy_add",
        {"session_id": first["session_id"], "instruction_id": 9, "amount": 1},
    )
    assert future["status"] == "rejected"
    assert "has not been issued" in future["message"]

    not_offered = reply(service, "toy_hidden", accepted)
    assert not_offered["status"] == "rejected"
    assert "not a reply to the pending instruction" in not_offered["message"]

    second = reply(service, "toy_add", accepted, amount=1)
    stale = reply(service, "toy_add", first, amount=5)
    assert second["instruction_id"] == 3
    assert stale["status"] == "stale"
    assert stale["instruction_id"] == 3
    assert service.show(first["session_id"])["data"]["total"] == 3


def test_completion_and_duplicate_completion(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service, target=2)
    done = reply(service, "toy_add", first, amount=2)
    assert done == {
        "status": "accepted",
        "session_id": first["session_id"],
        "completed": True,
        "result": {"total": 2},
    }
    assert reply(service, "toy_add", first, amount=2)["status"] == "duplicate"
    after = service.call(
        "toy_add",
        {"session_id": first["session_id"], "instruction_id": 2, "amount": 1},
    )
    assert after["status"] == "rejected"
    assert "completed" in after["message"]
    assert service.show(first["session_id"])["status"] == "completed"


def test_rejections_and_not_ready_save_nothing(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    schema = reply(service, "toy_add", first, amount="many")
    assert schema["status"] == "rejected"
    assert "amount" in schema["message"]
    rejected = reply(service, "toy_add", first, amount=-1)
    assert rejected["status"] == "rejected"
    assert rejected["message"] == "amount must not be negative"
    not_ready = reply(service, "toy_wait", first)
    assert not_ready["status"] == "not_ready"
    assert not_ready["instruction_id"] == 1
    assert not_ready["instruction"]["text"] == "## Add\n\nAdd a number."
    error = reply(service, "toy_boom", first)
    assert error["status"] == "error"
    assert "RuntimeError: kaboom" in error["message"]
    assert error["instruction_id"] == 1
    assert path.read_text() == before
    assert reply(service, "toy_add", first, amount=1)["status"] == "accepted"


def test_invalid_correlation_arguments(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service)
    with pytest.raises(ValidationError, match="Invalid session ID"):
        service.call("toy_add", {"session_id": "../x", "instruction_id": 1})
    with pytest.raises(ValidationError, match="instruction_id"):
        service.call("toy_add", {"session_id": first["session_id"], "amount": 1})
    with pytest.raises(SessionNotFoundError):
        service.call("toy_add", {"session_id": "missing", "instruction_id": 1})


def test_ordinary_tools(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    assert service.call("toy_peek", {"value": 3}) == {
        "status": "ok",
        "result": {"value": 3},
    }
    assert service.call("toy_peek", {})["status"] == "rejected"


def test_restart_recovery_continues_from_disk(config: HirakuConfig) -> None:
    first = started(WorkflowService(config), target=4)
    restarted = WorkflowService(HirakuConfig.discover(config.root))
    shown = restarted.show(first["session_id"])
    assert shown["last_response"]["instruction"] == first["instruction"]
    assert shown["orientation"] == first["orientation"]
    assert reply(restarted, "toy_add", first, amount=1)["status"] == "accepted"


def test_concurrent_replies_apply_once(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service, target=10)
    barrier = threading.Barrier(4)
    results: list[str] = []

    def worker() -> None:
        barrier.wait()
        results.append(reply(service, "toy_add", first, amount=1)["status"])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["accepted", "duplicate", "duplicate", "duplicate"]
    assert service.show(first["session_id"])["data"]["total"] == 1


def test_failed_save_keeps_previous_state(config: HirakuConfig, monkeypatch) -> None:
    service = WorkflowService(config)
    first = started(service)
    path = config.sessions_dir / f"{first['session_id']}.json"
    before = path.read_text()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.workflow.sessions.atomic_write", fail)
    with pytest.raises(ValidationError, match="disk full"):
        reply(service, "toy_add", first, amount=1)
    monkeypatch.undo()
    assert path.read_text() == before
    assert reply(service, "toy_add", first, amount=1)["status"] == "accepted"


def test_incompatible_sessions_are_blocked_but_inspectable(
    config: HirakuConfig,
) -> None:
    first = started(WorkflowService(config))
    toy = config.workflows_dir / "toy" / "workflow.py"
    toy.write_text(
        toy.read_text().replace(
            "    total: int = 0\n", "    total: int = 0\n    new: str\n"
        )
    )
    service = WorkflowService(config)
    blocked = reply(service, "toy_add", first, amount=1)
    assert blocked["status"] == "blocked"
    assert "no longer matches" in blocked["message"]
    assert "new" in service.show(first["session_id"])["compatibility"]

    toy.write_text(TOY_PY.replace("    def wait(", "    def waiting("))
    service = WorkflowService(config)
    blocked = reply(service, "toy_add", first, amount=1)
    assert blocked["status"] == "blocked"
    assert "pending replies wait" in blocked["message"]


def test_inspection_and_cancellation_without_the_workflow(
    config: HirakuConfig,
) -> None:
    first = started(WorkflowService(config))
    (config.workflows_dir / "toy" / "workflow.py").write_text("raise RuntimeError\n")
    service = WorkflowService(config)
    shown = service.show(first["session_id"])
    assert shown["workflow_available"] is False
    assert "unavailable" in shown["note"]
    assert shown["data"] == {"target": 3, "total": 0}
    cancelled = service.cancel(first["session_id"])
    assert cancelled["status"] == "cancelled"
    assert "does not revert code" in cancelled["note"]
    shown = service.show(first["session_id"])
    assert shown["status"] == "cancelled"
    assert "no longer actionable" in shown["note"]
    assert service.cancel(first["session_id"])["status"] == "cancelled"


def test_cancelled_sessions_reject_replies(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service)
    service.cancel(first["session_id"])
    response = reply(service, "toy_add", first, amount=1)
    assert response["status"] == "rejected"
    assert "cancelled" in response["message"]


def test_cannot_cancel_completed_session(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = started(service, target=1)
    reply(service, "toy_add", first, amount=1)
    with pytest.raises(ValidationError, match="already completed"):
        service.cancel(first["session_id"])


def test_malformed_session_files(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    started(service)
    (config.sessions_dir / "broken.json").write_text("{not json")
    listing = service.list_sessions()
    assert {"session_id": "broken", "error": listing[-1]["error"]} in listing
    with pytest.raises(ValidationError, match="malformed"):
        service.show("broken")


@pytest.mark.parametrize(
    ("workflows", "expected"),
    [
        (
            None,
            {"autodataset", "autoresearch", "guided-research", "hiraku-agent", "toy"},
        ),
        ([], set()),
        (["toy"], {"toy"}),
    ],
)
def test_configured_exposure(
    config: HirakuConfig, workflows: list[str] | None, expected: set[str]
) -> None:
    config.set_mcp_workflows(workflows)
    service = WorkflowService(HirakuConfig.discover(config.root))
    exposed = {item["name"].split("_")[0] for item in service.tool_definitions()}
    assert exposed == expected
    assert {item["id"] for item in service.list_workflows()} == {
        "autodataset",
        "autoresearch",
        "guided-research",
        "hiraku-agent",
        "toy",
    }


def test_unknown_configured_workflows_are_errors(config: HirakuConfig) -> None:
    config.set_mcp_workflows(["toy", "ghost"])
    with pytest.raises(WorkflowError, match="Unknown workflows in the MCP workflow"):
        WorkflowService(HirakuConfig.discover(config.root))
    with pytest.raises(WorkflowError, match="ghost"):
        WorkflowService(config, exposed=["ghost"])


def test_activation_persists_and_keeps_sessions(config: HirakuConfig) -> None:
    config.set_mcp_workflows(["autoresearch"])
    service = WorkflowService(HirakuConfig.discover(config.root))
    first = started(service)
    result = service.deactivate("toy")
    assert result == {"active": ["autoresearch"], "changed": False, "saved": False}
    result = service.activate("toy")
    assert result["changed"] is True
    assert "reconnect" in result["note"]
    assert HirakuConfig.discover(config.root).mcp.workflows == ["autoresearch", "toy"]
    service.deactivate("toy")
    assert HirakuConfig.discover(config.root).mcp.workflows == ["autoresearch"]
    shown = service.show(first["session_id"])
    assert shown["status"] == "active"
    assert "not active" in shown["note"]
    with pytest.raises(WorkflowError, match="Unknown workflow"):
        service.activate("ghost")


def test_cli_matches_service(config: HirakuConfig, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    arguments = tmp_path / "arguments.json"
    arguments.write_text(json.dumps({"target": 2}))
    result = runner.invoke(
        main, ["workflow", "call", "toy_start", "--arguments-file", str(arguments)]
    )
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["status"] == "accepted"
    session_id = response["session_id"]
    assert response == {
        **WorkflowService(config).show(session_id)["last_response"],
        "status": "accepted",
        "guide": response["guide"],
        "orientation": "# Toy\n\nAdd numbers.",
    }

    follow = json.dumps({"session_id": session_id, "instruction_id": 1, "amount": 1})
    result = runner.invoke(
        main, ["workflow", "call", "toy_add", "--arguments-file", "-"], input=follow
    )
    assert json.loads(result.output)["status"] == "accepted"

    listing = runner.invoke(main, ["workflow", "list", "--json"])
    assert {item["id"] for item in json.loads(listing.output)} >= {"toy"}
    shown = runner.invoke(main, ["session", "show", session_id])
    assert "Pending instruction 2" in shown.output
    assert "toy_add" in shown.output
    sessions = json.loads(runner.invoke(main, ["session", "list", "--json"]).output)
    assert sessions[0]["session_id"] == session_id
    cancelled = runner.invoke(main, ["session", "cancel", session_id])
    assert "cancelled" in cancelled.output
    assert runner.invoke(main, ["workflow", "deactivate", "toy"]).exit_code == 0
    assert "toy" not in HirakuConfig.discover(config.root).mcp.workflows
    unknown = runner.invoke(main, ["workflow", "call", "nope_start"])
    assert unknown.exit_code != 0
    assert "Unknown workflow tool" in unknown.output


def test_tool_names_can_differ_from_method_names(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    workflow = service.workflow("toy").bind(config)
    assert handlers(type(workflow))["save"].method == "save_total"
    assert service.tool("toy_save")[1].method == "save_total"
    model = session_model(type(workflow))
    assert model is not None
    session = model(target=3)
    instruction = workflow.instruction(
        session, "Done?", replies=[workflow.save_total, workflow.add]
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
    service = WorkflowService(config)
    first = started(service, target=10)
    assert reply(service, "toy_add", first, amount=3)["instruction"]["context"] == {
        "total": 6
    }
    assert service.workflow("toy").to_dict()["note"] == "kept"

    path.write_text("---\nid: toy\nstep: many\n---\n" + TOY_MD)
    error = load_error(config, "toy")
    assert "Invalid workflow settings" in error
    assert "step" in error


def test_calls_get_isolated_copies_of_the_definition(config: HirakuConfig) -> None:
    service = WorkflowService(config)
    first = service.workflow("toy").bind(config)
    assert first.id == "toy"
    assert first.config is config
    first.step = 5
    first.markdown = "# Changed\n"
    second = service.workflow("toy").bind(config)
    assert second.step == 1
    assert second.prompt("#add") == "## Add\n\nAdd a number."
    assert (
        not (config.workflows_dir / "toy" / "workflow.md")
        .read_text()
        .startswith("# Changed")
    )


def test_markdown_only_workflows_send_the_whole_prompt(config: HirakuConfig) -> None:
    directory = config.workflows_dir / "loop"
    directory.mkdir()
    body = "# Loop\n\nRun until stopped.\n\n## Steps\n\nDo things.\n"
    (directory / "workflow.md").write_text(
        "---\nid: loop\ndescription: Run the loop.\n---\n" + body
    )
    service = WorkflowService(config)
    workflow = service.workflows["loop"]
    assert type(workflow) is PromptWorkflow
    assert workflow_description(workflow) == "Run the loop."
    names = {item["name"] for item in service.tool_definitions()}
    assert {name for name in names if name.startswith("loop_")} == {
        "loop_start",
        "loop_complete",
    }

    first = service.call("loop_start", {"request": "Five experiments"})
    assert first["status"] == "accepted"
    assert first["orientation"] == "# Loop\n\nRun until stopped."
    assert first["instruction"]["text"] == body.strip()
    assert first["instruction"]["context"] == {"request": "Five experiments"}
    assert [item["tool"] for item in first["instruction"]["replies"]] == [
        "loop_complete"
    ]
    assert reply(service, "loop_complete", first, summary=" ")["status"] == "rejected"
    done = reply(service, "loop_complete", first, summary="Tried three ideas")
    assert done["result"] == {"summary": "Tried three ideas"}
    assert service.show(first["session_id"])["data"] == {
        "request": "Five experiments",
        "summary": "Tried three ideas",
    }

    without_request = service.call("loop_start", {})
    assert without_request["instruction"]["context"] is None

    (directory / "workflow.md").write_text(body)
    assert "front matter" in load_error(config, "loop")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "    @resource('notes')\n    def notes(self, x: str) -> str: ...\n",
            "takes no parameters besides self",
        ),
        (
            "    @mcp_prompt('ask')\n    def ask(self, count: int) -> str: ...\n",
            "must be a str",
        ),
        (
            "    @tool\n    @resource\n    def both(self) -> str: ...\n",
            "only one of a tool, resource, or MCP prompt",
        ),
        (
            "    @resource('a')\n    def one(self) -> str: ...\n"
            "    @resource('a')\n    def two(self) -> str: ...\n",
            "Resource name 'a' is used by both",
        ),
    ],
)
def test_invalid_resources_and_prompts(
    config: HirakuConfig, body: str, message: str
) -> None:
    source = (
        "from hiraku.workflow import Workflow, mcp_prompt, resource, tool\n"
        "class W(Workflow):\n"
        "    @tool\n"
        "    def start(self) -> None: ...\n"
        f"{body}"
        "WORKFLOW = W\n"
    )
    write_workflow(config, "exposed", source)
    assert message in load_error(config, "exposed")


def test_workflow_resources_and_prompts(config: HirakuConfig) -> None:
    source = TOY_PY.replace(
        "from hiraku.workflow import Session, Workflow, tool",
        "from hiraku.workflow import Session, Workflow, mcp_prompt, resource, tool",
    ).replace(
        "WORKFLOW = Toy",
        "    @resource('notes', mime_type='text/plain')\n"
        "    def notes_text(self) -> str:\n"
        '        """Step size notes."""\n'
        "        return f'step={self.step} root={self.config.root.name}'\n"
        "\n"
        "    @mcp_prompt\n"
        "    def greet(self, name: str, mood: str | None = None) -> str:\n"
        '        """Greet someone."""\n'
        "        return f'Hello {name} ({mood})'\n"
        "\n"
        "\n"
        "WORKFLOW = Toy",
    )
    (config.workflows_dir / "toy" / "workflow.py").write_text(source)
    service = WorkflowService(config)
    resources = {item["uri"]: item for item in service.resource_definitions()}
    notes = resources["hiraku://workflows/toy/notes"]
    assert notes["mime_type"] == "text/plain"
    assert notes["description"] == "Step size notes."
    assert service.read_resource("toy", "notes") == (
        f"step=1 root={config.root.name}",
        "text/plain",
    )
    prompts = {item["name"]: item for item in service.prompt_definitions()}
    assert prompts["toy_greet"]["arguments"] == [
        {"name": "name", "description": None, "required": True},
        {"name": "mood", "description": None, "required": False},
    ]
    assert service.get_prompt("toy_greet", {"name": "Ada"}) == "Hello Ada (None)"
    with pytest.raises(WorkflowError, match="name"):
        service.get_prompt("toy_greet", {})
    with pytest.raises(WorkflowError, match="no resource"):
        service.read_resource("toy", "missing")

    service.deactivate("toy")
    assert "hiraku://workflows/toy/notes" not in {
        item["uri"] for item in service.resource_definitions()
    }
    with pytest.raises(WorkflowError, match="not active"):
        service.read_resource("toy", "notes")
    with pytest.raises(WorkflowError, match="not active"):
        service.get_prompt("toy_greet", {"name": "Ada"})
