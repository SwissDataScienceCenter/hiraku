"""Count steps as a LangGraph workflow with a harness handoff per step."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic import ValidationError as PydanticValidationError

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.markdown import Markdown, render


class CounterState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    count: int = 0
    step: int = 0
    token: str = ""
    decision: str = ""
    response: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    final: str = ""
    completed: int = 0


class CountStepsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=1, description="Token shown in the current step.")
    decision: Literal["continue", "finish"] = Field(
        description="Continue to the next step or finish the workflow."
    )


def _input_model(default_count: int) -> type[BaseModel]:
    return create_model(
        "CountStepsInput",
        __config__=ConfigDict(extra="forbid", strict=True),
        count=(int, Field(default=default_count, ge=1)),
    )


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting count-steps."""
    fields, _ = BaseRecord._parse_markdown(Path(__file__).with_name("workflow.md"))
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict) or type(defaults.get("count")) is not int:
        raise ValueError("workflow.md must define an integer arguments.count default")
    if defaults["count"] < 1:
        raise ValueError("workflow.md arguments.count must be greater than zero")
    return _input_model(defaults["count"]).model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the example graph using the project's workflow.md instructions."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = BaseRecord._parse_markdown(path)
    if fields.get("id") != "count-steps":
        raise ValueError("workflow.md must declare id: count-steps")
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict) or type(defaults.get("count")) is not int:
        raise ValueError("workflow.md must define an integer arguments.count default")
    if defaults["count"] < 1:
        raise ValueError("workflow.md arguments.count must be greater than zero")
    markdown = Markdown(body)
    input_model = _input_model(defaults["count"])

    def initialize(state: CounterState) -> dict[str, Any]:
        try:
            arguments = input_model.model_validate(state.arguments)
        except PydanticValidationError as exc:
            if any(
                error["loc"] == ("count",) and error["type"] == "greater_than_equal"
                for error in exc.errors()
            ):
                raise ValidationError("count must be a positive integer") from exc
            raise ValidationError(str(exc)) from exc
        return {
            "count": arguments.count,
            "step": 1,
            "token": secrets.token_hex(4),
        }

    def handoff(state: CounterState) -> dict[str, Any]:
        values = {
            "step": state.step,
            "count": state.count,
            "token": state.token,
            "workflow_session_id": state.run_id,
        }
        payload = {
            "instruction": "\n\n".join(
                (
                    render(markdown.section("## Step"), values),
                    render(markdown.section("## Next"), values),
                )
            ),
            "data": values,
            "response_schema": CountStepsResponse.model_json_schema(),
            "required_capabilities": ["workflow.user_input"],
        }
        if state.error:
            payload["error"] = state.error
        return {"response": interrupt(payload)}

    def validate_response(state: CounterState) -> dict[str, Any]:
        try:
            response = CountStepsResponse.model_validate(state.response)
        except PydanticValidationError as exc:
            return {"error": f"Response does not match the expected schema: {exc}"}
        if response.token != state.token:
            return {"error": f"Token does not match the current step {state.step}."}
        return {"decision": response.decision, "error": ""}

    def next_step(state: CounterState) -> dict[str, Any]:
        return {"step": state.step + 1, "token": secrets.token_hex(4)}

    def finish(state: CounterState) -> dict[str, Any]:
        values = {"steps": state.step, "count": state.count}
        return {
            "completed": state.step,
            "final": render(markdown.section("## Done"), values),
        }

    def after_handoff(state: CounterState) -> str:
        if state.error:
            return "retry"
        if state.decision == "finish" or state.step >= state.count:
            return "finish"
        return "next"

    builder = StateGraph(CounterState)
    builder.add_node("initialize", initialize)
    builder.add_node("harness_step", handoff)
    builder.add_node("validate_response", validate_response)
    builder.add_node("next_step", next_step)
    builder.add_node("finish", finish)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "harness_step")
    builder.add_edge("harness_step", "validate_response")
    builder.add_conditional_edges(
        "validate_response",
        after_handoff,
        {"retry": "harness_step", "next": "next_step", "finish": "finish"},
    )
    builder.add_edge("next_step", "harness_step")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)
