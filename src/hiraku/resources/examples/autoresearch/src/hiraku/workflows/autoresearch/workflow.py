"""Return the autoresearch procedure as a single prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import ValidationError
from hiraku.markdown import Markdown
from hiraku.project import Project


class AutoresearchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    final: str = ""


class AutoresearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting autoresearch."""
    return AutoresearchInput.model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, project: Project) -> Any:
    """Build the prompt-only workflow graph."""
    post = frontmatter.load(Path(__file__).with_name("workflow.md"))
    prompt = str(Markdown(post.content)).strip()

    def validate_input(state: AutoresearchState) -> dict[str, Any]:
        try:
            AutoresearchInput.model_validate(state.arguments)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return {}

    def deliver(_: AutoresearchState) -> dict[str, str]:
        return {"final": prompt}

    builder = StateGraph(AutoresearchState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("deliver", deliver)
    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "deliver")
    builder.add_edge("deliver", END)
    return builder.compile(checkpointer=checkpointer)
