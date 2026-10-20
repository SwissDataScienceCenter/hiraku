"""Graph compilation without Workflow-specific models or inheritance."""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError
from typing import Annotated

import pytest
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import WorkflowError
from hiraku.workflows import graph as graph_module
from hiraku.workflows.graph import (
    EventArguments,
    State,
    WorkflowGraph,
    event,
    node,
    start,
)


class Count(State):
    node: str = "working"
    total: int = 0


class Settings(BaseModel):
    label: str


class Message(BaseModel):
    text: str


def test_argument_factory_from_function():
    def submit(
        self,
        state: Count,
        *,
        settings: Settings,
        count: Annotated[int, "Item count", Field(gt=0)] = 3,
    ) -> Count:
        return state

    model = EventArguments.from_function(submit)
    assert issubclass(model, EventArguments)
    assert model.__name__ == "submitEventArguments"
    assert set(model.model_fields) == {"settings", "count"}
    field = model.model_json_schema()["properties"]["count"]
    assert field["description"] == "Item count"
    assert field["default"] == 3
    assert field["exclusiveMinimum"] == 0
    parsed = model.from_text({"settings": '{"label": "test"}'})
    assert isinstance(parsed.settings, Settings)
    assert parsed.count == 3
    with pytest.raises(PydanticValidationError):
        model.model_validate({"settings": {"label": "test"}, "count": 0})
    with pytest.raises(PydanticValidationError):
        model.model_validate({"settings": {"label": "test"}, "extra": True})


def test_argument_factory_without_keyword_inputs():
    def finish(self, state: Count) -> Count:
        return state

    model = EventArguments.from_function(finish)
    assert model.model_fields == {}
    assert model.model_validate({}).model_dump() == {}


def test_argument_factory_uses_calling_class_as_base():
    class CustomArguments(EventArguments):
        enabled: bool = True

    def submit(*, count: int = 1):
        pass

    model = CustomArguments.from_function(submit)
    assert issubclass(model, CustomArguments)
    assert model.model_validate({}).model_dump() == {"enabled": True, "count": 1}


def test_argument_factory_requires_annotations():
    def submit(*, count):
        pass

    with pytest.raises(WorkflowError, match="submit.count must be an annotated"):
        EventArguments.from_function(submit)


@pytest.fixture
def definition():
    class Counter:
        def __init__(self):
            raise AssertionError("Compilation must not instantiate the class")

        @start(dest="working")
        def start(
            self, *, total: Annotated[int, "Initial count", Field(ge=0)] = 0
        ) -> Count:
            return Count(total=total)

        @node("working")
        def working(self, state: Count) -> Message:
            return Message(text=str(state.total))

        @node("done", final=True)
        def done(self, state: Count) -> Message:
            return Message(text="Done")

        @event(source="working", dest="working")
        def save(self, state: Count, *, settings: Settings) -> Count:
            return state

        @event(source="working", dest="done")
        def finish(self, state: Count) -> Count:
            return state

    return Counter


def test_compile_plain_class_with_pydantic_models(definition):
    graph = WorkflowGraph.from_class(definition)
    assert graph.state_model is Count
    assert graph.state_model().node == "working"
    assert graph.start.handler is definition.start
    assert graph.nodes["working"].handler is definition.working
    assert graph.nodes["working"].name == "working"
    assert graph.nodes["working"].final is False
    assert graph.nodes["done"].final is True
    with pytest.raises(FrozenInstanceError):
        graph.nodes["done"].final = False
    assert list(graph.events) == ["start", "save", "finish"]
    assert [item.name for item in graph.events_from("working")] == ["save", "finish"]
    assert graph.events["finish"].arguments.model_fields == {}
    graph.check_reachability()
    assert "working --> working: save" in graph.to_mermaid()
    with pytest.raises(TypeError):
        graph.events["other"] = graph.start


def test_plain_class_inputs_keep_defaults_constraints_and_models(definition):
    graph = WorkflowGraph.from_class(definition)
    model = graph.start.arguments
    assert model.model_validate({}).total == 0
    schema = model.model_json_schema()["properties"]["total"]
    assert schema["description"] == "Initial count"
    assert schema["minimum"] == 0
    assert schema["default"] == 0
    parsed = graph.events["save"].arguments.from_text({"settings": '{"label": "test"}'})
    assert isinstance(parsed.settings, Settings)
    assert parsed.settings.label == "test"


def test_plain_class_inherits_and_overrides_handlers(definition):
    class Child(definition):
        @event(source="working", dest="working")
        def save(self, state: Count, *, label: str) -> Count:
            return state

    parent, child = (
        WorkflowGraph.from_class(definition),
        WorkflowGraph.from_class(Child),
    )
    assert list(child.events) == list(parent.events)
    assert child.events["save"].handler is Child.save
    assert child.events["save"].handler is not parent.events["save"].handler
    assert set(child.events["save"].arguments.model_fields) == {"label"}


@pytest.mark.parametrize(
    ("handler", "parameter", "annotation"),
    [
        ("start", "return", int),
        ("start", "return", Message),
        ("start", "return", None),
        ("finish", "return", Message),
        ("finish", "state", Message),
        ("working", "state", Message),
        ("working", "return", str),
        ("working", "return", None),
    ],
)
def test_plain_class_rejects_invalid_annotations(
    definition, handler, parameter, annotation
):
    getattr(definition, handler).__annotations__[parameter] = annotation
    with pytest.raises(WorkflowError):
        WorkflowGraph.from_class(definition)


def test_plain_class_rejects_positional_external_inputs(definition):
    class Invalid(definition):
        @event(source="working", dest="done")
        def finish(self, state: Count, amount: int) -> Count:
            return state

    with pytest.raises(WorkflowError, match="keyword-only"):
        WorkflowGraph.from_class(Invalid)


def test_plain_class_rejects_async_handlers(definition):
    class Invalid(definition):
        @node("done", final=True)
        async def done(self, state: Count) -> Message:
            return Message(text="Done")

    with pytest.raises(WorkflowError, match="synchronous instance method"):
        WorkflowGraph.from_class(Invalid)


def test_plain_class_keeps_structural_validation(definition):
    class Invalid(definition):
        @event(source="working", dest="missing")
        def finish(self, state: Count) -> Count:
            return state

    with pytest.raises(WorkflowError, match="unknown nodes"):
        WorkflowGraph.from_class(Invalid)


def test_plain_class_reachability_is_optional(definition):
    class Disconnected(definition):
        @node("unused", final=True)
        def unused(self, state: Count) -> Message:
            return Message(text="Unused")

    graph = WorkflowGraph.from_class(Disconnected)
    with pytest.raises(WorkflowError, match="Unreachable nodes: .*unused"):
        graph.check_reachability()


def test_transition_checks_model_and_declared_destinations(definition):
    graph = WorkflowGraph.from_class(definition)
    graph.check_transition(graph.start, Count())
    graph.check_transition(graph.events["finish"], Count(node="done"))
    with pytest.raises(WorkflowError, match="start must return Count"):
        graph.check_transition(graph.start, Message(text="wrong model"))
    with pytest.raises(WorkflowError, match="finish returned node 'working'"):
        graph.check_transition(graph.events["finish"], Count())
    with pytest.raises(WorkflowError, match="start returned node 'missing'"):
        graph.check_transition(graph.start, Count(node="missing"))


def test_transition_requires_exact_model(definition):
    class ChildCount(Count):
        pass

    graph = WorkflowGraph.from_class(definition)
    with pytest.raises(WorkflowError, match="start must return Count"):
        graph.check_transition(graph.start, ChildCount())


def test_state_is_a_graph_abstraction():
    from hiraku.workflows import State as PublicState

    assert PublicState is State
    assert State.__module__ == "hiraku.workflows.graph"
    assert State(node="working").model_dump() == {"node": "working"}
    assert State(node="working", extra=1).model_dump() == {
        "node": "working",
        "extra": 1,
    }
    stored = State(node="working")
    with pytest.raises(PydanticValidationError):
        stored.node = "done"


def test_graph_has_no_imports_from_workflow_implementation():
    tree = ast.parse(inspect.getsource(graph_module))
    for statement in ast.walk(tree):
        if isinstance(statement, ast.ImportFrom):
            assert statement.level != 1, (
                "graph must not import sibling workflow modules"
            )
            assert not (statement.module or "").startswith("hiraku.workflows")
        elif isinstance(statement, ast.Import):
            assert all(
                not alias.name.startswith("hiraku.workflows")
                for alias in statement.names
            )
