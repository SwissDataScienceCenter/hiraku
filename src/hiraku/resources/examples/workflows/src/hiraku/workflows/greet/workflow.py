"""Render a greeting prompt in a single LangGraph node."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import ValidationError
from hiraku.markdown import Markdown, render
from hiraku.project import Project


class GreetState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    final: str = ""


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, project: Project) -> Any:
    """Build the one-step graph from the example's Markdown prompt."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = frontmatter.parse(path.read_text(encoding="utf-8"))
    if fields.get("id") != "greet":
        raise ValueError("workflow.md must declare id: greet")
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict) or not defaults:
        raise ValueError("workflow.md must define arguments")

    argument_model = _argument_model(defaults)
    markdown = Markdown(body)

    def greet(state: GreetState) -> dict[str, str]:
        try:
            arguments = argument_model.model_validate(state.arguments)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return {"final": render(markdown, arguments.model_dump())}

    builder = StateGraph(GreetState)
    builder.add_node("greet", greet)
    builder.add_edge(START, "greet")
    builder.add_edge("greet", END)
    return builder.compile(checkpointer=checkpointer)


def _argument_model(defaults: dict[str, Any]) -> type[BaseModel]:
    return create_model(
        "GreetArguments",
        __config__=ConfigDict(extra="forbid", strict=True),
        **{name: (type(value), value) for name, value in defaults.items()},
    )


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting greet."""
    fields, _ = frontmatter.parse(
        Path(__file__).with_name("workflow.md").read_text(encoding="utf-8")
    )
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict) or not defaults:
        raise ValueError("workflow.md must define arguments")
    return _argument_model(defaults).model_json_schema()
