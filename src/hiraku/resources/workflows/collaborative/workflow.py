"""Collaborative research as a checkpointed LangGraph workflow."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import GitRepository
from hiraku.markdown import Markdown, render
from hiraku.record import Record, load_record
from hiraku.run import Run
from hiraku.validation import validate_command

Verdict = Literal["supports", "refutes", "inconclusive"]
STEPS = ("draft", "implement", "launch", "analyze")


class ResearchState(TypedDict, total=False):
    arguments: dict[str, Any]
    task: str
    command: list[str]
    rounds: int
    step: str
    experiment: str
    feedback: str
    results: list[dict[str, Any]]
    final: str


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the collaborative research graph from its Markdown instructions."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = BaseRecord._parse_markdown(path)
    if fields.get("id") != "collaborative":
        raise ValueError("workflow.md must declare id: collaborative")
    markdown = Markdown(body)
    rules = str(fields.get("rules", "")).strip()

    def initialize(state: ResearchState) -> Command[Literal["draft"]]:
        arguments = state.arguments
        if not isinstance(arguments, dict):
            raise ValidationError("arguments must be an object")
        unknown = set(arguments) - {"task", "command", "rounds"}
        if unknown:
            raise ValidationError(f"Unknown arguments: {', '.join(sorted(unknown))}")
        task = arguments.get("task") or config.active_record_id
        command = arguments.get("command")
        rounds = arguments.get("rounds", 3)
        if not isinstance(task, str) or not task:
            raise ValidationError("Pass task or set an active Hiraku record")
        if not isinstance(command, list):
            raise ValidationError("command must be an argv list")
        command = validate_command(command)
        if type(rounds) is not int or rounds < 1:
            raise ValidationError("rounds must be a positive integer")
        try:
            load_record(task, config=config)
        except HirakuError as exc:
            raise ValidationError(str(exc)) from exc
        return Command(
            update={
                "task": task,
                "command": command,
                "rounds": rounds,
                "step": "draft",
                "results": [],
            },
            goto="draft",
        )

    def prompt(state: ResearchState, step: str, data: dict[str, Any]) -> dict[str, Any]:
        text = markdown.section(f"## {step.title()}")
        if rules:
            text = f"{text}\n\n{rules}"
        payload: dict[str, Any] = {
            "step": step,
            "instruction": text,
            "data": data,
            "approval": {
                "message": f"Approve the proposed {step} step?",
                "schema": WorkflowApproval.model_json_schema(),
            },
            "response_schema": {
                "event": "one of the listed events",
                "arguments": "object matching the selected event's fields",
            },
        }
        if state.feedback:
            payload["feedback"] = state.feedback
        return payload

    def response_for(
        state: ResearchState, step: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        events = {
            "draft": ("drafted", "revise_draft", "finish"),
            "implement": ("implemented", "revise_implementation", "finish"),
            "launch": ("launched", "revise_launch", "finish"),
            "analyze": ("analyzed", "revise_analysis", "finish"),
        }[step]
        payload = prompt(state, step, data)
        payload["response_schema"]["event"] = " | ".join(events)
        schemas = {
            "drafted": {
                "record_id": "short experiment record ID",
                "summary": "one-line summary",
                "hypothesis": "testable hypothesis",
                "change": "planned code change",
            },
            "revise_draft": {"feedback": "requested draft changes"},
            "implemented": {"commit": "commit containing the implementation"},
            "revise_implementation": {"feedback": "requested implementation changes"},
            "revise_launch": {"feedback": "requested launch changes"},
            "revise_analysis": {"feedback": "requested analysis changes"},
            "analyzed": {
                "verdict": "supports, refutes, or inconclusive",
                "conclusion": "evidence-based conclusion",
            },
        }
        payload["response_schema"]["arguments"] = {
            event: schemas.get(event, {}) for event in events
        }
        while True:
            response = interrupt(payload)
            approval = response.get("approval") if isinstance(response, dict) else None
            decision = approval.get("decision") if isinstance(approval, dict) else None
            if not isinstance(decision, str) or decision not in {
                "approve",
                "revise",
                "stop",
            }:
                payload = {**payload, "error": "A valid approval decision is required."}
                continue
            if decision == "stop":
                return {"event": "finish", "arguments": {}}
            if decision == "revise":
                submitted = response.get("response", {})
                if not isinstance(submitted, dict):
                    submitted = {}
                arguments = submitted.get("arguments", {})
                if not isinstance(arguments, dict):
                    arguments = {}
                feedback = approval.get("feedback")
                if feedback is None and isinstance(arguments, dict):
                    feedback = arguments.get("feedback")
                if feedback is None:
                    feedback = "Please revise this proposal."
                if not isinstance(feedback, str) or not feedback.strip():
                    payload = {
                        **payload,
                        "error": "Revision feedback must be non-empty.",
                    }
                    continue
                revise_events = {
                    "draft": "revise_draft",
                    "implement": "revise_implementation",
                    "launch": "revise_launch",
                    "analyze": "revise_analysis",
                }
                response = {
                    "event": revise_events[step],
                    "arguments": {"feedback": feedback},
                }
            else:
                response = response.get("response", response)
            if not isinstance(response, dict) or response.get("event") not in events:
                payload = {
                    **prompt(state, step, data),
                    "error": "Choose a valid event.",
                }
                payload["response_schema"]["event"] = " | ".join(events)
                continue
            arguments = response.get("arguments", {})
            if not isinstance(arguments, dict):
                payload = {
                    **prompt(state, step, data),
                    "error": "arguments must be an object.",
                }
                payload["response_schema"]["event"] = " | ".join(events)
                continue
            return {"event": response["event"], "arguments": arguments}

    def finish(state: ResearchState) -> Command[Literal["done"]]:
        return Command(update={"step": "done"}, goto="done")

    def draft(state: ResearchState) -> Command[Literal["draft", "implement", "done"]]:
        data = {"task": state.task, "previous": state.results}
        response = response_for(state, "draft", data)
        event, arguments = response["event"], response["arguments"]
        if event == "finish":
            return finish(state)
        if event == "revise_draft":
            feedback = arguments.get("feedback")
            if not isinstance(feedback, str):
                return Command(
                    update={"feedback": "Feedback must be a string."}, goto="draft"
                )
            return Command(update={"feedback": feedback}, goto="draft")
        required = ("record_id", "summary", "hypothesis", "change")
        if any(
            not isinstance(arguments.get(key), str) or not arguments[key].strip()
            for key in required
        ):
            return Command(
                update={
                    "feedback": (
                        "Provide non-empty record_id, summary, hypothesis, and change."
                    )
                },
                goto="draft",
            )
        try:
            record = Record.create(
                arguments["record_id"],
                parent_id=state.task,
                type="experiment",
                status="drafted",
                summary=arguments["summary"],
                hypothesis=arguments["hypothesis"],
                markdown=f"## Change\n\n{arguments['change'].strip()}\n",
                activate=False,
                config=config,
            )
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="draft")
        return Command(
            update={"experiment": record.id, "feedback": "", "step": "implement"},
            goto="implement",
        )

    def experiment(state: ResearchState) -> Record:
        identifier = state.experiment
        if not identifier:
            raise ValidationError("No experiment has been drafted")
        return load_record(identifier, config=config)

    def experiment_context(state: ResearchState) -> dict[str, Any]:
        record = experiment(state)
        return {
            "task": state.task,
            "experiment": {
                "record_id": record.id,
                "summary": getattr(record, "summary", None),
                "hypothesis": getattr(record, "hypothesis", None),
                "change": record.markdown.strip(),
                "commit": record.commit,
            },
        }

    def implement(
        state: ResearchState,
    ) -> Command[Literal["implement", "launch", "done"]]:
        response = response_for(state, "implement", experiment_context(state))
        event, arguments = response["event"], response["arguments"]
        if event == "finish":
            return finish(state)
        if event == "revise_implementation":
            feedback = arguments.get("feedback")
            return Command(
                update={
                    "feedback": feedback
                    if isinstance(feedback, str)
                    else "Feedback must be a string."
                },
                goto="implement",
            )
        commit = arguments.get("commit")
        if not isinstance(commit, str):
            return Command(
                update={"feedback": "commit must be a Git commit reference."},
                goto="implement",
            )
        try:
            sha = GitRepository(config).resolve(commit)
            record = experiment(state)
            record.commit = sha
            record.status = "implemented"
            record.save()
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="implement")
        return Command(update={"feedback": "", "step": "launch"}, goto="launch")

    def launch(state: ResearchState) -> Command[Literal["launch", "analyze", "done"]]:
        record = experiment(state)
        data = experiment_context(state)
        data["command"] = shlex.join(
            ["hiraku", "run", "--record", record.id, "--", *state["command"]]
        )
        response = response_for(state, "launch", data)
        event, arguments = response["event"], response["arguments"]
        if event == "finish":
            return finish(state)
        if event == "revise_launch":
            feedback = arguments.get("feedback")
            return Command(
                update={
                    "feedback": feedback
                    if isinstance(feedback, str)
                    else "Feedback must be a string."
                },
                goto="launch",
            )
        runs = _runs(record, config)
        if not runs:
            return Command(
                update={"feedback": f"No run is attributed to {record.id!r} yet."},
                goto="launch",
            )
        if any(run.commit not in (None, record.commit) for run in runs):
            return Command(
                update={
                    "feedback": "A run used a different commit than this experiment."
                },
                goto="launch",
            )
        record.status = "launched"
        record.save()
        return Command(update={"feedback": "", "step": "analyze"}, goto="analyze")

    def analyze(state: ResearchState) -> Command[Literal["analyze", "draft", "done"]]:
        record = experiment(state)
        runs = _runs(record, config)
        if not any(run.metrics for run in runs):
            return Command(
                update={"feedback": "The attributed runs have no metrics yet."},
                goto="analyze",
            )
        data = {
            **experiment_context(state),
            "runs": [{"run_id": run.id, "metrics": dict(run.metrics)} for run in runs],
            "previous": state.results,
        }
        response = response_for(state, "analyze", data)
        event, arguments = response["event"], response["arguments"]
        if event == "finish":
            return finish(state)
        if event == "revise_analysis":
            feedback = arguments.get("feedback")
            return Command(
                update={
                    "feedback": feedback
                    if isinstance(feedback, str)
                    else "Feedback must be a string."
                },
                goto="analyze",
            )
        verdict, conclusion = arguments.get("verdict"), arguments.get("conclusion")
        if (
            verdict not in {"supports", "refutes", "inconclusive"}
            or not isinstance(conclusion, str)
            or not conclusion.strip()
        ):
            return Command(
                update={"feedback": "Provide a verdict and non-empty conclusion."},
                goto="analyze",
            )
        record.status = "analyzed"
        record.verdict = verdict
        record.conclusion = conclusion
        record.save()
        results = [
            *state.results,
            {"record_id": record.id, "verdict": verdict},
        ]
        next_step = "draft" if len(results) < state.rounds else "done"
        return Command(
            update={
                "results": results,
                "experiment": "",
                "feedback": "",
                "step": next_step,
            },
            goto=next_step,
        )

    def done(state: ResearchState) -> dict[str, str]:
        return {
            "final": render(markdown.section("## Done"), {"results": state.results})
        }

    builder = StateGraph(ResearchState)
    builder.add_node("initialize", initialize)
    builder.add_node("draft", draft)
    builder.add_node("implement", implement)
    builder.add_node("launch", launch)
    builder.add_node("analyze", analyze)
    builder.add_node("done", done)
    builder.add_edge(START, "initialize")
    builder.add_edge("done", END)
    return builder.compile(checkpointer=checkpointer)


def _runs(record: Record, config: HirakuConfig) -> list[Run]:
    return record.runs(config=config)


class WorkflowApproval(BaseModel):
    """Decision required before a collaborative workflow step can proceed."""

    model_config = ConfigDict(
        json_schema_extra=lambda schema: schema.pop("title", None)
    )

    decision: Literal["approve", "revise", "stop"] = Field(
        description="Whether to approve, request a revision, or stop."
    )
    feedback: str | None = Field(
        default=None, description="Required guidance when requesting a revision."
    )
