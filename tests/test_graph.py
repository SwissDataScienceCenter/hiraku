"""Graph compilation without Workflow-specific models or inheritance."""

from __future__ import annotations

import ast
import inspect
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from hiraku.errors import WorkflowError
from hiraku.workflows import graph as graph_module
from hiraku.workflows.graph import WorkflowGraph, event, phase, start


class Count(BaseModel):
    total: int = 0


class Settings(BaseModel):
    label: str


class Message(BaseModel):
    text: str


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

        @phase("working")
        def working(self, state: Count) -> Message:
            return Message(text=str(state.total))

        @phase("done", final=True)
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
    assert "phase" not in graph.state_model.model_fields
    assert graph.start.handler is definition.start
    assert graph.phases["working"].handler is definition.working
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
        @phase("done", final=True)
        async def done(self, state: Count) -> Message:
            return Message(text="Done")

    with pytest.raises(WorkflowError, match="synchronous instance method"):
        WorkflowGraph.from_class(Invalid)


def test_plain_class_keeps_structural_validation(definition):
    class Invalid(definition):
        @event(source="working", dest="missing")
        def finish(self, state: Count) -> Count:
            return state

    with pytest.raises(WorkflowError, match="unknown phases"):
        WorkflowGraph.from_class(Invalid)


def test_plain_class_reachability_is_optional(definition):
    class Disconnected(definition):
        @phase("unused", final=True)
        def unused(self, state: Count) -> Message:
            return Message(text="Unused")

    graph = WorkflowGraph.from_class(Disconnected)
    with pytest.raises(WorkflowError, match="Unreachable phases: .*unused"):
        graph.check_reachability()


def test_graph_has_no_imports_from_workflow_implementation():
    tree = ast.parse(inspect.getsource(graph_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level != 1, "graph must not import sibling workflow modules"
            assert not (node.module or "").startswith("hiraku.workflows")
        elif isinstance(node, ast.Import):
            assert all(
                not alias.name.startswith("hiraku.workflows") for alias in node.names
            )
