from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner
from filelock import Timeout
from pydantic import ValidationError as PydanticValidationError

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import SessionNotFoundError, ValidationError, WorkflowError
from hiraku.workflows import (
    Event,
    EventArguments,
    EventResult,
    Instruction,
    MarkdownWorkflow,
    Node,
    State,
    WorkflowGraph,
    list_sessions,
    list_workflows,
    load_session,
    load_workflow,
)

TOY = '''from typing import Annotated, Literal
from pydantic import Field
from hiraku.errors import EventRejected, NotReady
from hiraku.workflows import State, Instruction, Workflow, node, event, start


class Total(State):
    node: Literal["adding", "done"] = "adding"
    total: int = 0
    target: int


class Toy(Workflow):
    minimum: int = 1

    @start(dest=["adding", "done"])
    def start(self, *, target: Annotated[int, Field(gt=0)] = 3) -> Total:
        """Start counting."""
        return Total(target=target)

    @node("adding")
    def adding(self, state: Total) -> Instruction:
        return Instruction(
            self.markdown.section("## Add"), context={"total": state.total}
        )

    @node("done", final=True)
    def done(self, state: Total) -> Instruction:
        return Instruction("Report the total.", context={"total": state.total})

    @event(source="adding", dest=["adding", "done"])
    def add(
        self, state: Total, *, amount: Annotated[int, Field(gt=0)], note: str = ""
    ) -> Total:
        """Submit an amount."""
        if note == "wait":
            raise NotReady("Evidence pending")
        if note == "reject":
            raise EventRejected("Try another amount")
        if note == "bug":
            raise RuntimeError("broken handler")
        total = state.total + amount
        return state.model_copy(
            update={
                "total": total,
                "node": "done" if total >= state.target else "adding",
            }
        )

    @event(source="adding", dest="done")
    def finish(self, state: Total) -> Total:
        return state.model_copy(update={"node": "done"})


WORKFLOW = Toy
'''


@pytest.fixture
def config(tmp_path, monkeypatch):
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    write_workflow(selected, TOY)
    return selected


def write_workflow(config, source, name="toy", markdown="## Add\n\nAdd an amount.\n"):
    directory = config.workflows_dir / name
    directory.mkdir(exist_ok=True)
    (directory / "workflow.py").write_text(source)
    (directory / "workflow.md").write_text(f"---\nid: {name}\n---\n{markdown}")
    return directory


def started(config, target=3):
    workflow = load_workflow("toy", config=config)
    result = workflow.run_start({"target": target})
    assert result.status == "accepted", result
    return workflow, result


def submit(workflow, result, amount=1, **arguments):
    return workflow.run_event(
        result.session_id,
        result.instruction_id,
        "add",
        {"amount": amount, **arguments},
    )


def test_graph_and_models_are_the_only_registry(config):
    workflow, result = started(config)
    graph = workflow.graph
    assert load_workflow("toy").graph is not graph
    assert graph.start is graph.events["start"]
    assert graph.start.arguments.__name__ == "startEventArguments"
    assert graph.state_model.__name__ == "Total"
    assert graph.events["finish"].arguments.model_fields == {}
    graph.check_reachability()
    assert [e.name for e in graph.events_from("adding")] == ["add", "finish"]
    with pytest.raises(TypeError):
        graph.nodes["other"] = graph.nodes["done"]
    state = graph.state_model(target=3)
    with pytest.raises(PydanticValidationError):
        state.total = 5
    assert workflow.start(target=2).target == 2
    assert isinstance(result, EventResult)
    assert "instruction" not in result.model_dump()
    assert (
        workflow.graph.events["add"].arguments.model_json_schema()[
            "additionalProperties"
        ]
        is False
    )


def test_state_updates_and_reads_are_snapshots(config):
    workflow, first = started(config)
    read = workflow.get_instruction(first.session_id)
    assert read == workflow.get_instruction(first.session_id)
    assert read["node"] == "adding"
    assert read["instruction"]["context"] == {"total": 0}
    assert read["instruction"]["events"]["instruction_id"] == 1
    second = submit(workflow, first)
    assert second.status == "accepted"
    assert second.instruction_id == 2
    assert workflow.get_instruction(first.session_id)["node"] == "adding"
    session = load_session(first.session_id, config=config)
    assert session.state == {"node": "adding", "total": 1, "target": 3}
    assert session.node == "adding"
    assert session.instruction.context == {"total": 1}
    assert len(session.history) == 2
    assert list_sessions(config=config)[0].id == first.session_id


def test_node_is_used_in_session_storage_and_inspection(config):
    workflow, result = started(config)
    session = load_session(result.session_id)
    stored = json.loads((config.sessions_dir / f"{session.id}.json").read_text())
    assert stored["format_version"] == 2
    assert stored["state"]["node"] == "adding"
    shown = session.show(workflow)
    assert shown["node"] == "adding"
    assert "phase" not in shown
    assert "phase" not in stored["state"]
    listing = CliRunner().invoke(main, ["session", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.output)[0]["node"] == "adding"


def test_old_session_format_is_rejected(config):
    _, result = started(config)
    path = config.sessions_dir / f"{result.session_id}.json"
    stored = json.loads(path.read_text())
    stored["format_version"] = 1
    stored["state"]["phase"] = stored["state"].pop("node")
    path.write_text(json.dumps(stored))
    with pytest.raises(ValidationError, match="Unsupported session format"):
        load_session(result.session_id)


def test_completion_and_retry(config):
    workflow, first = started(config, target=1)
    done = submit(workflow, first)
    assert done.status == "accepted"
    assert submit(workflow, first) == done
    read = workflow.get_instruction(done.session_id)
    assert read["session_status"] == "completed"
    assert "events" not in read["instruction"]
    assert read["instruction"]["context"] == {"total": 1}
    assert submit(workflow, done).status == "rejected"
    with pytest.raises(ValidationError):
        load_session(done.session_id).cancel()


@pytest.mark.parametrize(
    ("arguments", "status"),
    [
        ({"amount": 0}, "rejected"),
        ({"amount": 1, "unknown": 2}, "rejected"),
        ({}, "rejected"),
        ({"amount": 1, "note": "reject"}, "rejected"),
        ({"amount": 1, "note": "wait"}, "not_ready"),
        ({"amount": 1, "note": "bug"}, "error"),
    ],
)
def test_failed_submissions_preserve_storage(config, arguments, status):
    workflow, first = started(config)
    path = config.sessions_dir / f"{first.session_id}.json"
    before = path.read_bytes()
    result = workflow.run_event(first.session_id, 1, "add", arguments)
    assert result.status == status
    assert result.instruction_id == 1
    assert path.read_bytes() == before
    assert "instruction" not in result.model_dump()


@pytest.mark.parametrize("arguments", [{"target": 0}, {"extra": True}])
def test_invalid_start_creates_no_session(config, arguments):
    assert load_workflow("toy", config=config).run_start(arguments).status == "rejected"
    assert list_sessions(config=config) == []


def test_omitted_arguments_and_wrong_node(config):
    workflow, first = started(config)
    wrong = workflow.run_event(first.session_id, 1, "finish", {"extra": True})
    assert wrong.status == "rejected"
    assert workflow.run_event(first.session_id, 1, "unknown").status == "rejected"
    assert workflow.run_event(first.session_id, 1, "start").status == "rejected"
    assert workflow.run_event(first.session_id, 1, "finish").status == "accepted"


def test_correlation_cancel_and_restart(config):
    workflow, first = started(config)
    second = submit(workflow, first)
    assert submit(workflow, first) == second
    assert submit(workflow, first, amount=2).status == "stale"
    assert (
        workflow.run_event(first.session_id, 99, "add", {"amount": 1}).status == "stale"
    )
    restarted = load_workflow("toy", config=config)
    third = submit(restarted, second)
    assert third.status == "accepted"
    session = load_session(first.session_id, config=config)
    session.cancel()
    assert submit(restarted, second).status == "rejected"
    assert "instruction" not in restarted.get_instruction(first.session_id)
    assert "instruction" not in load_session(first.session_id).show()
    assert session.cancel()["status"] == "cancelled"


@pytest.mark.parametrize("session_id", ["../escape", "", "/absolute", "x/y"])
def test_invalid_session_ids(config, session_id):
    with pytest.raises(ValidationError):
        load_session(session_id)
    with pytest.raises(ValidationError):
        load_workflow("toy").run_event(session_id, 1, "finish")


@pytest.mark.parametrize("revision", [None, True, "1", 0, -1])
def test_invalid_revision(config, revision):
    workflow, first = started(config)
    with pytest.raises(ValidationError):
        workflow.run_event(first.session_id, revision, "finish")


def test_missing_session(config):
    with pytest.raises(SessionNotFoundError):
        load_session("missing")


def test_concurrent_submission_applies_once(config):
    workflow, first = started(config)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(workflow, first), range(2)))
    assert results[0] == results[1]
    assert load_session(first.session_id).state["total"] == 1


def test_lock_timeout_is_retryable(config, monkeypatch):
    workflow, first = started(config)

    def busy(path):
        raise Timeout(str(path))

    monkeypatch.setattr("hiraku.workflows.session._lock", busy)
    assert submit(workflow, first).status == "not_ready"


def test_failed_save_preserves_previous_session(config, monkeypatch):
    workflow, first = started(config)
    before = load_session(first.session_id).model_dump()

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.workflows.session.Session._save", fail)
    assert submit(workflow, first).status == "error"
    assert load_session(first.session_id).model_dump() == before


@pytest.mark.parametrize(
    "replacement",
    [
        'return state.model_copy(update={"node": "unknown"})',
        'return state.model_copy(update={"total": "invalid"})',
        "return None",
        'return state.model_copy(update={"node": "done", "total": float("nan")})',
    ],
)
def test_returned_state_is_validated_before_saving(config, replacement):
    source = TOY.replace(
        """return state.model_copy(
            update={
                "total": total,
                "node": "done" if total >= state.target else "adding",
            }
        )""",
        replacement,
    )
    assert source != TOY
    write_workflow(config, source)
    workflow, first = started(config)
    before = load_session(first.session_id).model_dump()
    assert submit(workflow, first).status == "error"
    assert load_session(first.session_id).model_dump() == before


@pytest.mark.parametrize(
    "expression",
    [
        "None",
        'Instruction(" ")',
        'Instruction("x", context=object())',
        'Instruction("x", events={"accepted": []})',
    ],
)
def test_bad_instruction_saves_nothing(config, expression):
    source = TOY.replace(
        'return Instruction("Report the total.", context={"total": state.total})',
        f"return {expression}",
    )
    write_workflow(config, source)
    workflow, first = started(config, target=1)
    assert submit(workflow, first).status == "error"
    assert load_session(first.session_id).state["total"] == 0


def test_instruction_mutation_cannot_change_saved_state(config):
    source = TOY.replace(
        'return Instruction(self.markdown.section("## Add"),',
        'object.__setattr__(state, "total", 99)\n'
        '        return Instruction(self.markdown.section("## Add"),',
    )
    write_workflow(config, source)
    workflow, first = started(config)
    assert load_session(first.session_id).state["total"] == 0


def test_config_reloads_and_is_bound_to_project(config, tmp_path, monkeypatch):
    workflow, first = started(config)
    assert workflow.config.active_record_id is None
    config.active_record_id = "changed"
    assert workflow.config.active_record_id is None
    config.save()
    assert workflow.config.active_record_id == "changed"
    other = tmp_path / "other"
    other.mkdir()
    HirakuConfig.init(other, install_skill=False)
    monkeypatch.chdir(other)
    assert workflow.config.path == config.path
    assert workflow.config.active_record_id == "changed"
    assert "config" not in workflow.model_dump()
    assert "_config_path" not in workflow.model_dump()
    assert "text" not in type(workflow).model_fields
    config.path.unlink()
    with pytest.raises(ValidationError):
        _ = workflow.config


def test_constructor_and_markdown_fields(config):
    cls = type(load_workflow("toy"))
    workflow = cls(id="direct", config=config, minimum=2, markdown="## Add\n\nHello")
    assert workflow.description is None
    assert workflow.minimum == 2
    assert workflow.markdown.section("## Add").startswith("## Add")
    assert workflow.config.path == config.path
    with pytest.raises(ValidationError, match="unique headings"):
        cls(id="duplicate", config=config, markdown="## X\n\n## X")


def test_workflow_settings_and_identity(config):
    directory = config.workflows_dir / "toy"
    (directory / "workflow.md").write_text("---\nid: toy\nminimum: nope\n---\n")
    with pytest.raises(ValidationError):
        load_workflow("toy")
    (directory / "workflow.md").write_text("---\nid: other\n---\n")
    with pytest.raises(ValidationError):
        load_workflow("toy")


@pytest.mark.parametrize("name", ["../bad", "has_underscore", "", "/absolute"])
def test_invalid_workflow_ids(config, name):
    with pytest.raises(WorkflowError):
        load_workflow(name)


def test_incomplete_workflows_and_listing(config, caplog):
    directory = write_workflow(config, TOY, name="broken")
    (directory / "workflow.py").write_text("raise RuntimeError('broken sidecar')")
    with pytest.raises(WorkflowError, match="broken sidecar"):
        load_workflow("broken")
    assert "toy" in [w.id for w in list_workflows(config=config)]
    assert "broken" in caplog.text
    (directory / "workflow.py").write_text(TOY)
    (directory / "workflow.md").unlink()
    with pytest.raises(Exception, match="record not found"):
        load_workflow("broken")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            '@start(dest=["adding", "done"])',
            '@event(source="adding", dest=["adding", "done"])',
        ),
        ("def start(", "def begin("),
        ("def start(", "async def start("),
        ('source="adding", dest="done"', 'source=None, dest="done"'),
        ('dest="done"', 'dest="missing"'),
        ('@node("done", final=True)', '@node("done")'),
        ('@node("done", final=True)', '@node("adding", final=True)'),
        (
            "def start(self, *, target: Annotated[int, Field(gt=0)] = 3) -> Total:",
            "def start(self, *, target: Annotated[int, Field(gt=0)] = 3) -> None:",
        ),
        (
            '*, amount: Annotated[int, Field(gt=0)], note: str = ""',
            'amount: Annotated[int, Field(gt=0)], note: str = ""',
        ),
        (
            "def add(",
            "async def add(",
        ),
        (
            "def finish(self, state: Total) -> Total:",
            "def finish(self, state: int) -> Total:",
        ),
        (
            "def finish(self, state: Total) -> Total:",
            "def finish(self, state: Total, *args) -> Total:",
        ),
        (
            "def finish(self, state: Total) -> Total:",
            "def finish(self, state: Total = None) -> Total:",
        ),
        (
            "def finish(self, state: Total) -> Total:",
            "def save(self, state: Total) -> Total:",
        ),
        ('@node("adding")', '@node("bad-name")'),
        ('@node("adding")', '@node("unused")'),
        (
            '@node("done", final=True)',
            '@node("done", final=True)\n    @event(source="adding", dest="done")',
        ),
        ('@start(dest=["adding", "done"])', ""),
    ],
)
def test_invalid_declarations_fail_when_loaded(config, old, new):
    assert old in TOY
    write_workflow(config, TOY.replace(old, new))
    with pytest.raises(WorkflowError):
        load_workflow("toy")


def test_workflow_requires_state_subclass(config):
    source = TOY.replace(
        "from pydantic import Field", "from pydantic import BaseModel, Field"
    ).replace("class Total(State):", "class Total(BaseModel):")
    write_workflow(config, source)
    with pytest.raises(WorkflowError, match="start must return a concrete State model"):
        load_workflow("toy")


def test_workflow_requires_instruction_node_return(config):
    write_workflow(
        config, TOY.replace("state: Total) -> Instruction:", "state: Total) -> Total:")
    )
    with pytest.raises(
        WorkflowError, match="Node handler adding must return Instruction"
    ):
        load_workflow("toy")


def test_workflow_rejects_reserved_node_handler_name(config):
    write_workflow(config, TOY.replace("def adding(", "def save("))
    with pytest.raises(WorkflowError, match="Invalid workflow handler: save"):
        load_workflow("toy")


@pytest.mark.parametrize(
    "parameter",
    [
        "value",
        "value: int",
        "*values: int",
        "**values: int",
        "*, value",
        "*, _private: int",
        "*, model_dump: str",
    ],
)
def test_invalid_event_input_parameters(config, parameter):
    source = TOY.replace("*, target: Annotated[int, Field(gt=0)] = 3", parameter)
    write_workflow(config, source)
    with pytest.raises(WorkflowError):
        load_workflow("toy")


def test_start_decorator_requires_start_method_name(config):
    source = TOY.replace(
        "node, event, start", "node, event, start as workflow_start"
    ).replace("@start(", "@workflow_start(")
    source = source.replace(
        '@event(source="adding", dest="done")', '@workflow_start(dest="done")'
    )
    write_workflow(config, source)
    with pytest.raises(WorkflowError, match="Only the start method can use @start"):
        load_workflow("toy")


def test_generated_inputs_preserve_constraints_and_defaults(config):
    workflow = load_workflow("toy")
    start = workflow.graph.start.arguments
    assert start.model_validate({}).target == 3
    assert workflow.start().target == 3
    assert start.model_json_schema()["properties"]["target"]["exclusiveMinimum"] == 0
    add = workflow.graph.events["add"].arguments
    assert add.model_json_schema()["required"] == ["amount"]
    assert add.model_validate({"amount": 1}).note == ""
    assert workflow.run_start({"target": 0}).status == "rejected"
    assert workflow.run_start({"extra": 1}).status == "rejected"
    assert workflow.run_start().status == "accepted"


def test_generated_inputs_preserve_nested_models_and_metadata(config):
    source = (
        TOY.replace(
            "from pydantic import Field", "from pydantic import BaseModel, Field"
        )
        .replace(
            "class Total(State):",
            "class Settings(BaseModel):\n    target: int\n\nclass Total(State):",
        )
        .replace(
            "target: Annotated[int, Field(gt=0)] = 3",
            'settings: Annotated[Settings, "Counter settings"]',
        )
        .replace("return Total(target=target)", "return Total(target=settings.target)")
    )
    write_workflow(config, source)
    workflow = load_workflow("toy")
    inputs = workflow.graph.start.arguments
    assert inputs.model_json_schema()["properties"]["settings"]["description"] == (
        "Counter settings"
    )
    parsed = inputs.from_text({"settings": '{"target": 2}'})
    assert parsed.settings.target == 2
    result = workflow.run_start({"settings": {"target": 2}})
    assert result.status == "accepted", result.message
    assert load_session(result.session_id).state["target"] == 2
    assert workflow.run_start({"settings": {}}).status == "rejected"


@pytest.mark.parametrize(
    "annotation",
    [
        'Annotated[int, "Target count", Field(gt=0)]',
        'Annotated[int, Field(gt=0), "Target count"]',
        'Annotated[int, "Target count", Field(gt=0, description="Old description")]',
        'Annotated[Annotated[int, "Target count"], Field(gt=0)]',
    ],
)
def test_string_descriptions_preserve_other_metadata(config, annotation):
    source = TOY.replace("Annotated[int, Field(gt=0)] = 3", f"{annotation} = 3")
    write_workflow(config, source)
    workflow = load_workflow("toy")
    field = workflow.graph.start.arguments.model_json_schema()["properties"]["target"]
    assert field["description"] == "Target count"
    assert field["exclusiveMinimum"] == 0
    assert field["default"] == 3
    assert workflow.run_start({"target": 0}).status == "rejected"
    assert workflow.run_start().status == "accepted"


def test_multiple_string_descriptions_rejected(config):
    source = TOY.replace(
        "Annotated[int, Field(gt=0)] = 3",
        'Annotated[int, "First", "Second"] = 3',
    )
    write_workflow(config, source)
    with pytest.raises(WorkflowError, match="start.target has multiple descriptions"):
        load_workflow("toy")


def test_start_without_inputs(config):
    source = TOY.replace(
        "self, *, target: Annotated[int, Field(gt=0)] = 3", "self"
    ).replace("return Total(target=target)", "return Total(target=3)")
    write_workflow(config, source)
    workflow = load_workflow("toy")
    assert workflow.graph.start.arguments.model_fields == {}
    assert workflow.run_start().status == "accepted"
    assert workflow.run_start({"target": 3}).status == "rejected"


def test_inherited_graph_is_independent(config):
    source = TOY.replace(
        "WORKFLOW = Toy",
        """class Child(Toy):
    @event(source="adding", dest="done")
    def finish(self, state: Total) -> Total:
        return state.model_copy(update={"node": "done", "total": 42})


WORKFLOW = Child
""",
    )
    write_workflow(config, source)
    workflow, first = started(config)
    parent = type(workflow).__mro__[1](id="parent", config=config)
    assert parent.graph is not workflow.graph
    assert (
        parent.graph.events["finish"].handler
        is not workflow.graph.events["finish"].handler
    )
    workflow.run_event(first.session_id, 1, "finish")
    assert load_session(first.session_id).state["total"] == 42


def test_relative_imports_and_reloading(config):
    directory = config.workflows_dir / "toy"
    (directory / "helper.py").write_text("VALUE = 7\n")
    write_workflow(
        config,
        "from .helper import VALUE\n"
        + TOY.replace("minimum: int = 1", "minimum: int = VALUE"),
    )
    assert load_workflow("toy").minimum == 7
    write_workflow(config, TOY.replace("minimum: int = 1", "minimum: int = 8"))
    assert load_workflow("toy").minimum == 8


def test_incompatible_code_is_inspectable_and_cancellable(config):
    workflow, first = started(config)
    write_workflow(config, TOY.replace("total: int = 0", "total: list[int]"))
    updated = load_workflow("toy")
    result = submit(updated, first)
    assert result.status == "rejected"
    assert "instruction" not in updated.get_instruction(first.session_id)
    assert "Incompatible" in load_session(first.session_id).show(updated)["message"]
    (config.workflows_dir / "toy" / "workflow.py").unlink()
    assert load_session(first.session_id).show()["state"]["total"] == 0
    assert load_session(first.session_id).cancel()["status"] == "cancelled"


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"id":"old"}', '{"format_version":99}']
)
def test_unsupported_or_invalid_storage(config, content, caplog):
    config.sessions_dir.mkdir(exist_ok=True)
    (config.sessions_dir / "old.json").write_text(content)
    with pytest.raises(ValidationError):
        load_session("old")
    assert list_sessions() == []
    assert "unavailable" in caplog.text


def test_single_final_node_and_dynamic_entry(config):
    source = TOY.replace(
        "return Total(target=target)",
        'return Total(target=target, node="done")',
    )
    write_workflow(config, source)
    workflow, first = started(config)
    assert load_session(first.session_id).status == "completed"
    assert workflow.get_instruction(first.session_id)["node"] == "done"


def test_graph_cli_and_receipt_only_submission(config):
    workflow, first = started(config)
    expected = (
        "stateDiagram-v2\n  [*] --> adding\n  [*] --> done\n"
        "  adding --> adding: add\n  adding --> done: add\n"
        "  adding --> done: finish\n  done --> [*]\n"
    )
    assert workflow.graph.to_mermaid() == expected
    runner = CliRunner()
    graph = runner.invoke(
        main, ["workflow", "graph", "toy", "--session", first.session_id]
    )
    assert graph.exit_code == 0, graph.output
    assert "class adding current" in graph.output
    sent = runner.invoke(
        main,
        [
            "workflow",
            "send-event",
            first.session_id,
            "finish",
            "--instruction-id",
            "1",
        ],
    )
    assert sent.exit_code == 0, sent.output
    assert "instruction" not in json.loads(sent.output)
    shown = runner.invoke(main, ["session", "show", first.session_id, "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["session_status"] == "completed"


def test_instruction_renders_itself():
    text = Instruction("Do work", context={"x": 1}, events={"accepted": []}).to_text()
    assert text.startswith("Do work\n\nContext:")
    assert "Events:" in text
    assert Instruction("Only text").to_text() == "Only text"


def test_base_models_reject_extras():
    with pytest.raises(PydanticValidationError):
        EventArguments(extra=1)
    with pytest.raises(PydanticValidationError):
        State(node="x", extra=1)


def test_arguments_from_text_preserves_strings_and_decodes_structured_values():
    class Inputs(EventArguments):
        name: str
        count: int = 3
        enabled: bool = False
        items: list[int]
        metadata: dict[str, int]
        choice: str | int = 0

    inputs = Inputs.from_text(
        {
            "name": "123",
            "enabled": "true",
            "items": "[1, 2]",
            "metadata": '{"score": 7}',
            "choice": "42",
        }
    )
    assert inputs.name == "123"
    assert inputs.count == 3
    assert inputs.enabled is True
    assert inputs.items == [1, 2]
    assert inputs.metadata == {"score": 7}
    assert inputs.choice == "42"
    assert (
        Inputs.from_text(
            {
                "name": "true",
                "count": "5",
                "items": "[]",
                "metadata": "{}",
            }
        ).count
        == 5
    )
    with pytest.raises(ValidationError, match="items: expected JSON"):
        Inputs.from_text({"name": "x", "items": "invalid", "metadata": "{}"})
    with pytest.raises(PydanticValidationError):
        Inputs.from_text({"name": "x", "items": "{}", "metadata": "{}"})
    with pytest.raises(PydanticValidationError):
        Inputs.from_text(
            {
                "name": "x",
                "items": "[]",
                "metadata": "{}",
                "extra": "x",
            }
        )


@pytest.mark.parametrize("field,value", [("state", {}), ("instruction", {"text": 7})])
def test_malformed_session_fields_are_reported(config, field, value, caplog):
    workflow, first = started(config)
    path = config.sessions_dir / f"{first.session_id}.json"
    raw = json.loads(path.read_text())
    raw[field] = value
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        load_session(first.session_id)
    assert list_sessions() == []
    assert "unavailable" in caplog.text


def test_cancel_lock_timeout_is_a_domain_error(config, monkeypatch):
    workflow, first = started(config)

    def busy(path):
        raise Timeout(str(path))

    monkeypatch.setattr("hiraku.workflows.session._lock", busy)
    with pytest.raises(ValidationError, match="busy"):
        load_session(first.session_id).cancel()


def test_invalid_saved_config_is_not_silently_replaced(config):
    workflow, first = started(config)
    config.path.write_text("- invalid\n- configuration\n")
    with pytest.raises(ValidationError):
        _ = workflow.config


def test_failed_save_result_reports_the_saved_revision(config, monkeypatch):
    workflow, first = started(config)

    def fail(self):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.workflows.session.Session._save", fail)
    result = submit(workflow, first)
    assert result.status == "error"
    assert result.instruction_id == first.instruction_id


def write_markdown(
    config, arguments="", body="Hello.", name="markdown", description=None
):
    directory = config.workflows_dir / name
    directory.mkdir(exist_ok=True)
    metadata = f"id: {name}\n"
    if description is not None:
        metadata += f"description: {description}\n"
    (directory / "workflow.md").write_text(f"---\n{metadata}{arguments}---\n{body}")
    return directory


def test_markdown_defaults_types_description_and_graph(config):
    write_markdown(
        config,
        "arguments:\n  name: World\n  num_iterations: 5\n"
        "  max_val: 0.5\n  gate: true\n",
        "{{name}}: {{num_iterations}}, {{max_val}}, {{gate}}.",
        description="A research prompt.",
    )
    workflow = load_workflow("markdown")
    assert isinstance(workflow, MarkdownWorkflow)
    assert workflow.description == "A research prompt."
    assert workflow.to_dict()["description"] == "A research prompt."
    assert workflow.graph.start.description == "A research prompt."
    model = workflow.graph.start.arguments
    assert {name: field.annotation for name, field in model.model_fields.items()} == {
        "name": str,
        "num_iterations": int,
        "max_val": float,
        "gate": bool,
    }
    assert "required" not in model.model_json_schema()
    result = workflow.run_start()
    assert result.status == "accepted"
    response = workflow.get_instruction(result.session_id)
    assert response["session_status"] == "completed"
    assert response["instruction"]["text"] == "World: 5, 0.5, true."
    assert "events" not in response["instruction"]
    assert workflow.graph.to_mermaid() == (
        "stateDiagram-v2\n  [*] --> done\n  done --> [*]\n"
    )


def test_markdown_overrides_and_text_inputs(config):
    write_markdown(
        config,
        "arguments:\n  count: 5\n  enabled: true\n",
        "{{count}} {{enabled}}",
    )
    workflow = load_workflow("markdown")
    args = workflow.graph.start.arguments.from_text({"count": "2", "enabled": "false"})
    result = workflow.run_start(args.model_dump())
    assert workflow.get_instruction(result.session_id)["instruction"]["context"] == {
        "count": 2,
        "enabled": False,
    }
    assert workflow.run_start({"extra": True}).status == "rejected"
    assert workflow.run_start({"count": "invalid"}).status == "rejected"
    assert len(list_sessions()) == 1


def test_markdown_without_arguments_and_saved_snapshot(config):
    directory = write_markdown(config, body="Original prompt.")
    workflow = load_workflow("markdown")
    assert workflow.description is None
    result = workflow.run_start()
    before = workflow.get_instruction(result.session_id)
    assert before["instruction"]["context"] == {}
    (directory / "workflow.md").write_text("---\nid: markdown\n---\nChanged prompt.")
    assert load_workflow("markdown").get_instruction(result.session_id) == before


@pytest.mark.parametrize(
    "arguments",
    [
        "arguments:\n  value: null\n",
        "arguments:\n  value: []\n",
        "arguments:\n  value: {}\n",
        "arguments:\n  _private: 1\n",
        "arguments:\n  bad-name: 1\n",
        "arguments:\n  class: 1\n",
        "arguments:\n  model_dump: 1\n",
        "arguments:\n  from_text: 1\n",
        "arguments: []\n",
    ],
)
def test_invalid_markdown_arguments_fail_when_loaded(config, arguments):
    write_markdown(config, arguments)
    with pytest.raises((WorkflowError, ValidationError)):
        load_workflow("markdown")


def test_markdown_missing_placeholder_saves_nothing(config):
    write_markdown(config, body="{{missing}}")
    result = load_workflow("markdown").run_start()
    assert result.status == "error"
    assert "No value for placeholders" in result.message
    assert list_sessions() == []


def test_python_sidecar_takes_precedence(config):
    directory = write_workflow(config, TOY, name="markdown")
    assert not isinstance(load_workflow("markdown"), MarkdownWorkflow)
    (directory / "workflow.py").write_text("raise RuntimeError('broken sidecar')")
    with pytest.raises(WorkflowError, match="broken sidecar"):
        load_workflow("markdown")


def test_markdown_instances_have_independent_schemas(config):
    write_markdown(config, "arguments:\n  count: 1\n", name="first")
    write_markdown(config, "arguments:\n  label: text\n", name="second")
    first, second = load_workflow("first"), load_workflow("second")
    assert first.graph is not second.graph
    assert set(first.graph.start.arguments.model_fields) == {"count"}
    assert set(second.graph.start.arguments.model_fields) == {"label"}


@pytest.mark.parametrize(
    "problem",
    [
        "missing_start",
        "bad_source",
        "unknown",
        "final_event",
        "empty_dest",
        "wrong_name",
        "missing_final",
        "wrong_node_name",
        "nonfinal_without_events",
    ],
)
def test_explicit_graphs_share_structural_validation(config, problem):
    workflow = MarkdownWorkflow(id="direct", config=config)
    nodes = dict(workflow.graph.nodes)
    events = dict(workflow.graph.events)
    if problem == "missing_start":
        events.clear()
    elif problem == "bad_source":
        events["start"] = Event(
            "start",
            ("done",),
            ("done",),
            EventArguments,
            "",
            MarkdownWorkflow.start,
        )
    elif problem == "unknown":
        events["start"] = Event(
            "start",
            None,
            ("missing",),
            EventArguments,
            "",
            MarkdownWorkflow.start,
        )
    elif problem == "final_event":
        events["again"] = Event(
            "again",
            ("done",),
            ("done",),
            EventArguments,
            "",
            MarkdownWorkflow.start,
        )
    elif problem == "empty_dest":
        events["start"] = Event(
            "start",
            None,
            (),
            EventArguments,
            "",
            MarkdownWorkflow.start,
        )
    elif problem == "wrong_name":
        events["other"] = events.pop("start")
    elif problem == "missing_final":
        nodes["done"] = Node("done", False, MarkdownWorkflow.prompt)
    elif problem == "wrong_node_name":
        nodes["other"] = nodes.pop("done")
    elif problem == "nonfinal_without_events":
        nodes["unused"] = Node("unused", False, MarkdownWorkflow.prompt)
    with pytest.raises(WorkflowError):
        WorkflowGraph(workflow.graph.state_model, nodes, events)


def test_explicit_graph_copies_definition_mappings(config):
    workflow = MarkdownWorkflow(id="direct", config=config)
    nodes, events = dict(workflow.graph.nodes), dict(workflow.graph.events)
    graph = WorkflowGraph(workflow.graph.state_model, nodes, events)
    nodes.clear()
    events.clear()
    assert set(graph.nodes) == {"done"}
    assert set(graph.events) == {"start"}
    assert graph.nodes["done"].final is True
    with pytest.raises(TypeError):
        graph.nodes["other"] = graph.nodes["done"]
    with pytest.raises(TypeError):
        graph.events["other"] = graph.start


@pytest.mark.parametrize("final", [True, False])
def test_reachability_is_an_optional_check(config, final):
    workflow = MarkdownWorkflow(id="direct", config=config)
    nodes, events = dict(workflow.graph.nodes), dict(workflow.graph.events)
    nodes["unused"] = Node("unused", final, MarkdownWorkflow.prompt)
    if not final:
        events["again"] = Event(
            "again",
            ("unused",),
            ("unused",),
            EventArguments,
            "",
            MarkdownWorkflow.start,
        )
    graph = WorkflowGraph(workflow.graph.state_model, nodes, events)
    assert "unused" in graph.nodes
    with pytest.raises(WorkflowError, match="Unreachable nodes: .*unused"):
        graph.check_reachability()


def test_decorated_workflow_can_load_with_unreachable_node(config):
    source = TOY.replace(
        "    minimum: int = 1",
        """    @node("unused", final=True)
    def unused(self, state: Total) -> Instruction:
        return Instruction("Unused.")""",
    )
    write_workflow(config, source)
    workflow, result = started(config)
    assert result.status == "accepted"
    with pytest.raises(WorkflowError, match="Unreachable nodes: .*unused"):
        workflow.graph.check_reachability()
