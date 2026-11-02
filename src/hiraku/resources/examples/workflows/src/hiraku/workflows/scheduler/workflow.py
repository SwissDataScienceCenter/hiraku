"""Schedule record updates through a LangGraph harness handoff."""

from __future__ import annotations

import json
import random
import secrets
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic import ValidationError as PydanticValidationError

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.markdown import Markdown, render
from hiraku.record import Record, load_record


class SchedulerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    parent: str = ""
    target: int = 0
    current: str | None = None
    baseline: int = 0
    priority: int = 0
    remaining: int = 0
    response: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    final: str = ""
    records: list[dict[str, Any]] = Field(default_factory=list)


class RecordUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str = Field(min_length=1, description="The assigned record ID.")


def _input_model(defaults: dict[str, Any]) -> type[BaseModel]:
    return create_model(
        "SchedulerInput",
        __config__=ConfigDict(extra="forbid", strict=True),
        records=(int, Field(default=defaults["records"], ge=1, le=20)),
        target=(int, Field(default=defaults["target"], ge=1, le=10)),
    )


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting scheduler."""
    fields, _ = Record._parse_markdown(Path(__file__).with_name("workflow.md"))
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict):
        raise ValueError("workflow.md must define scheduler argument defaults")
    for name, lower, upper in (("records", 1, 20), ("target", 1, 10)):
        value = defaults.get(name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(
                f"workflow.md arguments.{name} must be an integer from "
                f"{lower} to {upper}"
            )
    return _input_model(defaults).model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the graph that creates and schedules experiment records."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = Record._parse_markdown(path)
    if fields.get("id") != "scheduler":
        raise ValueError("workflow.md must declare id: scheduler")
    defaults = fields.get("arguments", {})
    if not isinstance(defaults, dict):
        raise ValueError("workflow.md must define scheduler argument defaults")
    for name, lower, upper in (("records", 1, 20), ("target", 1, 10)):
        value = defaults.get(name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(
                f"workflow.md arguments.{name} must be an integer from "
                f"{lower} to {upper}"
            )
    input_model = _input_model(defaults)
    markdown = Markdown(body)

    def initialize(state: SchedulerState) -> dict[str, Any]:
        try:
            arguments = input_model.model_validate(state.arguments)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        records, target = arguments.records, arguments.target

        parent = Record.create(
            f"scheduler-{secrets.token_hex(4)}",
            type="task",
            summary=f"Scheduler example: raise each count to {target}",
            config=config,
        )
        for index in range(records):
            Record.create(
                f"{parent.id}-{index + 1}",
                parent_id=parent.id,
                type="experiment",
                priority=random.randint(1, 9),
                count=0,
                config=config,
            )
        return {"parent": parent.id, "target": target}

    def assign(state: SchedulerState) -> dict[str, Any]:
        children = _children(state.parent, config)
        open_records = [item for item in children if _count(item) < state.target]
        selected = max(
            open_records,
            key=lambda item: (_priority(item), item.id),
            default=None,
        )
        if selected is None:
            return {
                "current": None,
                "baseline": 0,
                "remaining": 0,
                "records": [
                    {
                        "record": item.id,
                        "priority": _priority(item),
                        "count": _count(item),
                    }
                    for item in children
                ],
            }
        return {
            "current": selected.id,
            "baseline": _count(selected),
            "priority": _priority(selected),
            "remaining": sum(
                state.target - _count(item)
                for item in children
                if _count(item) < state.target
            ),
            "error": "",
        }

    def handoff(state: SchedulerState) -> dict[str, Any]:
        record_id = state.current
        values = {
            "record": record_id,
            "count": state.baseline,
            "next": state.baseline + 1,
            "workflow_session_id": state.run_id,
        }
        payload: dict[str, Any] = {
            "instruction": render(markdown.section("## Work"), values),
            "data": {
                **values,
                "priority": state.priority,
                "remaining": state.remaining,
                "parent": state.parent,
            },
            "response_schema": RecordUpdateResponse.model_json_schema(),
            "required_capabilities": ["record.update"],
        }
        if state.error:
            payload["error"] = state.error
        return {"response": interrupt(payload)}

    def validate_update(state: SchedulerState) -> dict[str, Any]:
        try:
            response = RecordUpdateResponse.model_validate(state.response)
        except PydanticValidationError as exc:
            return {"error": f"Response does not match the expected schema: {exc}"}
        if response.record_id != state.current:
            return {"error": f"Report the assigned record {state.current!r}."}
        record = load_record(str(state.current), config=config)
        count = _count(record)
        expected = state.baseline + 1
        if count != expected:
            return {"error": f"The count is {count}; set it to {expected}."}
        return {"error": ""}

    def route_update(state: SchedulerState) -> str:
        return "retry" if state.error else "assign"

    def finish(state: SchedulerState) -> dict[str, Any]:
        values = {"records": json.dumps(state.records, indent=2)}
        return {"final": render(markdown.section("## Done"), values)}

    builder = StateGraph(SchedulerState)
    builder.add_node("initialize", initialize)
    builder.add_node("assign", assign)
    builder.add_node("harness_step", handoff)
    builder.add_node("validate_update", validate_update)
    builder.add_node("finish", finish)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "assign")
    builder.add_conditional_edges(
        "assign",
        lambda state: "finish" if state.current is None else "work",
        {"finish": "finish", "work": "harness_step"},
    )
    builder.add_edge("harness_step", "validate_update")
    builder.add_conditional_edges(
        "validate_update",
        route_update,
        {"retry": "harness_step", "assign": "assign"},
    )
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def _children(parent_id: str, config: HirakuConfig) -> list[Record]:
    return sorted(
        load_record(parent_id, config=config).children(config=config),
        key=lambda item: item.id,
    )


def _priority(record: Record) -> int:
    return int(getattr(record, "priority", 0))


def _count(record: Record) -> int:
    value = getattr(record, "count", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"The count of {record.id!r} must be an integer, not {value!r}"
        )
    return value
