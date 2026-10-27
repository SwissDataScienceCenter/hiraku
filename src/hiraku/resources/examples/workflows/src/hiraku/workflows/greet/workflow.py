"""Render a greeting prompt in a single LangGraph node."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ConfigDict, create_model
from pydantic import ValidationError as PydanticValidationError

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.markdown import Markdown, render


class GreetState(TypedDict, total=False):
    arguments: dict[str, Any]
    final: str


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the one-step graph from the example's Markdown prompt."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = BaseRecord._parse_markdown(path)
    if fields.get("id") != "greet":
        raise ValueError("workflow.md must declare id: greet")
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict) or not defaults:
        raise ValueError("workflow.md must define arguments")

    argument_model = create_model(
        "GreetArguments",
        __config__=ConfigDict(extra="forbid"),
        **{name: (type(value), value) for name, value in defaults.items()},
    )
    markdown = Markdown(body)

    def greet(state: GreetState) -> GreetState:
        try:
            arguments = argument_model.model_validate(state.get("arguments", {}))
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return {"final": render(markdown, arguments.model_dump())}

    builder = StateGraph(GreetState)
    builder.add_node("greet", greet)
    builder.add_edge(START, "greet")
    builder.add_edge("greet", END)
    return builder.compile(checkpointer=checkpointer)
