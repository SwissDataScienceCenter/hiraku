"""Return the autoresearch procedure as a single prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.markdown import Markdown


class AutoresearchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    final: str = ""


class AutoresearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting autoresearch."""
    return AutoresearchInput.model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the prompt-only workflow graph."""
    _, body = BaseRecord._parse_markdown(Path(__file__).with_name("workflow.md"))
    prompt = str(Markdown(body)).strip()

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
