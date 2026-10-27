"""Count steps as a LangGraph workflow with a harness handoff per step."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.markdown import Markdown, render


class CounterState(TypedDict, total=False):
    arguments: dict[str, Any]
    run_id: str
    count: int
    step: int
    token: str
    decision: str
    response: dict[str, Any]
    error: str
    final: str
    completed: int


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

    def initialize(state: CounterState) -> CounterState:
        arguments = state.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValidationError("arguments must be an object")
        unknown = set(arguments) - set(defaults)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValidationError(f"Unknown workflow arguments: {names}")
        count = arguments.get("count", defaults["count"])
        if type(count) is not int or count < 1:
            raise ValidationError("count must be a positive integer")
        return {
            "count": count,
            "step": 1,
            "token": secrets.token_hex(4),
            "run_id": state["run_id"],
        }

    def handoff(state: CounterState) -> CounterState:
        values = {
            "step": state["step"],
            "count": state["count"],
            "token": state["token"],
            "run_id": state["run_id"],
        }
        payload = {
            "instruction": "\n\n".join(
                (
                    render(markdown.section("## Step"), values),
                    render(markdown.section("## Next"), values),
                )
            ),
            "data": values,
            "response_schema": {
                "token": "string: token shown in the current step",
                "decision": "continue or finish",
            },
        }
        if state.get("error"):
            payload["error"] = state["error"]
        return {"response": interrupt(payload)}

    def validate_response(state: CounterState) -> CounterState:
        response = state["response"]
        if not isinstance(response, dict):
            return {"error": "Response must be an object with token and decision."}
        if response.get("token") != state["token"]:
            return {"error": f"Token does not match the current step {state['step']}."}
        if response.get("decision") not in {"continue", "finish"}:
            return {"error": "Decision must be 'continue' or 'finish'."}
        return {"decision": response["decision"], "error": ""}

    def next_step(state: CounterState) -> CounterState:
        return {"step": state["step"] + 1, "token": secrets.token_hex(4)}

    def finish(state: CounterState) -> CounterState:
        values = {"steps": state["step"], "count": state["count"]}
        return {
            "completed": state["step"],
            "final": render(markdown.section("## Done"), values),
        }

    def after_handoff(state: CounterState) -> str:
        if state.get("error"):
            return "retry"
        if state["decision"] == "finish" or state["step"] >= state["count"]:
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
