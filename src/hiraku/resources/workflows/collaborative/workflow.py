"""Collaborative research as a checkpointed LangGraph workflow."""

from __future__ import annotations

import json
import shlex
from functools import reduce
from hashlib import sha256
from operator import or_
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import HirakuError, ValidationError
from hiraku.execution import validate_command
from hiraku.git import GitRepository
from hiraku.markdown import Markdown, render
from hiraku.project import Project
from hiraku.record import Record, load_record
from hiraku.run import Run

Verdict = Literal["supports", "refutes", "inconclusive"]


class ResearchState(TypedDict, total=False):
    arguments: dict[str, Any]
    task: str
    command: list[str]
    rounds: int
    step: str
    experiment: str
    draft_revision: str
    proposed_plan: str
    proposed_plan_revision: str
    approved_plan: str
    proposed_launch_command: str
    proposed_launch_revision: str
    approved_launch_command: str
    launched_run_id: str
    feedback: str
    results: list[dict[str, Any]]
    final: str


class CollaborativeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: str | None = Field(
        default=None, description="Task record ID; defaults to active context."
    )
    command: list[str] = Field(min_length=1)
    rounds: int = Field(default=3, ge=1)


class DraftedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    change: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)


class FeedbackArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    feedback: str = Field(min_length=1)


class ImplementedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    commit: str = Field(min_length=1)


class ImplementationPlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plan: str = Field(min_length=1)


class PlanRevisionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: str = Field(min_length=1)


class LaunchPlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: str = Field(min_length=1)


class WorkflowApproval(BaseModel):
    """Decision required before a protected workflow transition."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "revise", "stop"] = Field(
        description="Whether to approve, request a revision, or stop."
    )
    revision: str | None = Field(
        default=None, description="Server-attached revision being authorized."
    )


class LaunchedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str = Field(min_length=1)


class AnalyzedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Verdict
    conclusion: str = Field(min_length=1)


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema for starting collaborative research."""
    return CollaborativeInput.model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, project: Project) -> Any:
    """Build the collaborative research graph from its Markdown instructions."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = Record._parse_markdown(path)
    if fields.get("id") != "collaborative":
        raise ValueError("workflow.md must declare id: collaborative")
    markdown = Markdown(body)
    rules = str(fields.get("rules", "")).strip()

    def initialize(state: ResearchState) -> Command[Literal["draft"]]:
        try:
            arguments = CollaborativeInput.model_validate(state.get("arguments", {}))
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        task = arguments.task or project.active_record_id
        command = arguments.command
        rounds = arguments.rounds
        if not isinstance(task, str) or not task:
            raise ValidationError("Pass task or set an active Hiraku record")
        if not isinstance(command, list):
            raise ValidationError("command must be an argv list")
        command = validate_command(command)
        if type(rounds) is not int or rounds < 1:
            raise ValidationError("rounds must be a positive integer")
        try:
            load_record(task, project=project)
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

    def prompt(
        state: ResearchState,
        step: str,
        data: dict[str, Any],
        *,
        mode: Literal["automatic", "review", "approval"],
        approval_message: str | None = None,
        interaction_type: Literal["task", "review", "approval"] | None = None,
    ) -> dict[str, Any]:
        section = {
            "draft": "## Draft and review",
            "draft_approval": "## Draft and review",
            "implement": "## Implementation",
            "implement_approval": "## Implementation",
            "implement_execution": "## Implementation",
            "launch": "## Launch",
            "launch_approval": "## Launch",
            "launch_execution": "## Launch",
            "analyze": "## Analyze, review, and iterate",
            "results_review": "## Analyze, review, and iterate",
            "next_iteration": "## Analyze, review, and iterate",
        }[step]
        text = markdown.section(section)
        if rules:
            text = f"{text}\n\n{rules}"
        interaction_type = interaction_type or (
            "approval" if mode == "approval" else "task"
        )
        payload: dict[str, Any] = {
            "step": step,
            "action": {
                "draft": "draft_experiment",
                "draft_approval": "approve_experiment_draft",
                "implement": (
                    "approve_implementation"
                    if mode == "approval"
                    else "prepare_implementation_plan"
                ),
                "implement_approval": "approve_implementation",
                "implement_execution": "implement_experiment",
                "launch": "approve_launch" if mode == "approval" else "prepare_launch",
                "launch_approval": "approve_launch",
                "launch_execution": "launch_experiment",
                "analyze": "analyze_results",
                "results_review": "review_results",
            }.get(step, step),
            "actor": "human" if mode == "approval" else "agent",
            "interaction_type": interaction_type,
            "instruction": text,
            "data": data,
            "interaction": {
                "type": interaction_type,
                "mode": mode,
                "allowed_decisions": {
                    "automatic": [],
                    "review": ["continue", "revise", "stop"],
                    "approval": ["approve", "revise", "stop"],
                }[mode],
            },
            "required_capabilities": {
                "draft": ["record.read", "workflow.user_input"],
                "draft_approval": ["workflow.user_input"],
                "implement": [
                    "filesystem.read",
                    "filesystem.write",
                    "shell.execute",
                    "git.commit",
                    "workflow.user_input",
                ],
                "launch": [
                    "experiment.launch",
                    "tracking.run.read",
                    "workflow.user_input",
                ],
                "analyze": ["tracking.run.read", "workflow.user_input"],
                "results_review": ["workflow.user_input"],
                "next_iteration": ["workflow.user_input"],
            }[step],
        }
        if mode == "approval":
            payload["approval"] = {
                "message": approval_message or f"Approve the proposed {step} action?",
                "schema": WorkflowApproval.model_json_schema(),
            }
        if state.get("feedback"):
            payload["feedback"] = state["feedback"]
        return payload

    def response_for(
        state: ResearchState, step: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        payload = prompt(state, step, data, mode="review")
        payload["phase"] = {
            "draft": "propose",
            "implement": "plan",
            "launch": "propose",
            "analyze": "analyze",
        }[step]
        action_adapter, response_schema = _step_response_contract(step, "review")
        payload["response_schema"] = response_schema
        while True:
            response = interrupt(payload)
            try:
                action = action_adapter.validate_python(response)
            except PydanticValidationError as exc:
                payload = {**payload, "error": str(exc)}
                continue
            return action.model_dump()

    def decision_for(
        state: ResearchState,
        step: str,
        data: dict[str, Any],
        *,
        expected_event: str | None = None,
        revision: str | None = None,
        interaction_type: Literal["review", "approval"] = "approval",
    ) -> tuple[str, str | None]:
        payload = prompt(
            state,
            step,
            data,
            mode="approval",
            approval_message=f"Approve this exact {step} proposal?",
            interaction_type=interaction_type,
        )
        payload["response_schema"] = _approval_response_schema(expected_event, revision)
        if revision is not None:
            payload["approval"]["revision"] = revision
        while True:
            response = interrupt(payload)
            try:
                approval = WorkflowApproval.model_validate(
                    response.get("approval") if isinstance(response, dict) else None
                )
            except PydanticValidationError as exc:
                payload = {**payload, "error": str(exc)}
                continue
            decision = approval.decision
            if decision == "stop":
                return "stop", None
            if decision == "revise":
                return "revise", "Please revise the proposal."
            if expected_event is None:
                return "approve", None
            approved_revision = approval.revision
            if approved_revision is None and isinstance(response, dict):
                # Accept the prior event-envelope format for persisted sessions.
                legacy_action = response.get("response")
                if isinstance(legacy_action, dict):
                    legacy_arguments = legacy_action.get("arguments")
                    if legacy_action.get("event") == expected_event and isinstance(
                        legacy_arguments, dict
                    ):
                        approved_revision = legacy_arguments.get("revision")
            if approved_revision != revision:
                payload = {
                    **payload,
                    "error": "Approval must reference the pending proposal revision.",
                }
                continue
            return "approve", None

    def execution_response_for(
        state: ResearchState, step: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        action_adapter, response_schema = _step_response_contract(step, "execution")
        instructions = {
            "implement": (
                "The implementation plan in data.approved_plan has been approved. "
                "Apply it, show the user the changes, commit them, and resume with "
                "the commit reference as the implemented result."
            ),
            "launch": (
                "The exact command in data.command has been approved. Before "
                "running it, inspect runs attributed to this experiment. If a run "
                "for the implemented commit and approved command already exists, "
                "do not launch it again; report its run ID. If the existing run "
                "cannot be matched confidently, ask the user before starting "
                "another run. Otherwise run the approved command, inspect its "
                "tracked result, and resume with its run ID."
            ),
        }
        payload = prompt(state, step, data, mode="automatic")
        payload.update(
            {
                "phase": "execute",
                "instruction": instructions[step],
                "response_schema": response_schema,
            }
        )
        if state.get("feedback"):
            payload["feedback"] = state["feedback"]
        while True:
            response = interrupt(payload)
            try:
                action = action_adapter.validate_python(response)
            except PydanticValidationError as exc:
                payload = {**payload, "error": str(exc)}
                continue
            return action.model_dump()

    def finish(state: ResearchState) -> Command[Literal["done"]]:
        return Command(update={"step": "done"}, goto="done")

    def draft(state: ResearchState) -> Command[Literal["draft", "implement", "done"]]:
        data = {
            "task": state["task"],
            "previous": state.get("results", []),
            "suggested_command": state["command"],
        }
        if state.get("experiment"):
            data["current_draft"] = experiment_context(state)["experiment"]
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
                        "Provide non-empty record_id, summary, hypothesis, and change, "
                        "plus a per-experiment command argv list."
                    )
                },
                goto="draft",
            )
        try:
            command = validate_command(arguments.get("command"))
            if command is None:
                raise ValidationError("A per-experiment command is required")
            existing_id = state.get("experiment")
            if existing_id:
                if arguments["record_id"] != existing_id:
                    raise ValidationError(
                        f"A revised draft must keep record_id {existing_id!r}"
                    )
                record = load_record(existing_id, project=project)
                record.summary = arguments["summary"]
                record.hypothesis = arguments["hypothesis"]
                record.command = command
                record.markdown = f"## Change\n\n{arguments['change'].strip()}\n"
                record.save()
            else:
                record = Record.create(
                    arguments["record_id"],
                    parent_id=state["task"],
                    type="experiment",
                    status="drafted",
                    summary=arguments["summary"],
                    hypothesis=arguments["hypothesis"],
                    command=command,
                    markdown=f"## Change\n\n{arguments['change'].strip()}\n",
                    activate=False,
                    project=project,
                )
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="draft")
        revision_data = {
            "record_id": record.id,
            "summary": arguments["summary"],
            "hypothesis": arguments["hypothesis"],
            "change": arguments["change"],
            "command": command,
        }
        revision = sha256(
            json.dumps(revision_data, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return Command(
            update={
                "experiment": record.id,
                "draft_revision": revision,
                "feedback": "",
                "step": "draft_approval",
            },
            goto="draft_approval",
        )

    def draft_approval(
        state: ResearchState,
    ) -> Command[Literal["draft", "implement", "done"]]:
        data = {
            **experiment_context(state),
            "proposal_revision": state.get("draft_revision", ""),
        }
        decision, feedback = decision_for(
            state,
            "draft_approval",
            data,
            expected_event="draft_approved",
            revision=state.get("draft_revision", ""),
            interaction_type="review",
        )
        if decision == "stop":
            return finish(state)
        if decision == "revise":
            return Command(
                update={"feedback": feedback or "Revise the experiment draft."},
                goto="draft",
            )
        return Command(update={"feedback": "", "step": "implement"}, goto="implement")

    def experiment(state: ResearchState) -> Record:
        identifier = state.get("experiment")
        if not identifier:
            raise ValidationError("No experiment has been drafted")
        return load_record(identifier, project=project)

    def experiment_context(state: ResearchState) -> dict[str, Any]:
        record = experiment(state)
        return {
            "task": state["task"],
            "experiment": {
                "record_id": record.id,
                "summary": getattr(record, "summary", None),
                "hypothesis": getattr(record, "hypothesis", None),
                "change": record.markdown.strip(),
                "command": getattr(record, "command", None),
                "commit": record.commit,
            },
        }

    def implement(
        state: ResearchState,
    ) -> Command[Literal["implement", "implement_approval", "done"]]:
        data = experiment_context(state)
        response = response_for(state, "implement", data)
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
        if event != "implementation_plan":
            return Command(
                update={"feedback": "Provide an implementation plan."},
                goto="implement",
            )
        plan = arguments["plan"]
        revision = sha256(plan.encode("utf-8")).hexdigest()
        return Command(
            update={
                "proposed_plan": plan,
                "proposed_plan_revision": revision,
                "feedback": "",
            },
            goto="implement_approval",
        )

    def implement_approval(
        state: ResearchState,
    ) -> Command[Literal["implement", "implement_execution", "done"]]:
        data = {
            **experiment_context(state),
            "proposed_plan": state.get("proposed_plan", ""),
            "plan_revision": state.get("proposed_plan_revision", ""),
        }
        decision, feedback = decision_for(
            state,
            "implement",
            data,
            expected_event="implementation_approved",
            revision=state.get("proposed_plan_revision", ""),
        )
        if decision == "stop":
            return finish(state)
        if decision == "revise":
            return Command(
                update={"feedback": feedback or "Revise the implementation plan."},
                goto="implement",
            )
        return Command(
            update={
                "approved_plan": state.get("proposed_plan", ""),
                "feedback": "",
            },
            goto="implement_execution",
        )

    def implement_execution(
        state: ResearchState,
    ) -> Command[Literal["implement", "launch", "done"]]:
        data = {
            **experiment_context(state),
            "approved_plan": state.get("approved_plan", ""),
        }
        response = execution_response_for(state, "implement", data)
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
            sha = GitRepository(project.root).resolve(commit)
            record = experiment(state)
            record.commit = sha
            record.status = "implemented"
            record.save()
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="implement")
        return Command(
            update={"approved_plan": "", "feedback": "", "step": "launch"},
            goto="launch",
        )

    def launch(
        state: ResearchState,
    ) -> Command[Literal["launch", "launch_approval", "done"]]:
        record = experiment(state)
        command = getattr(record, "command", None) or state["command"]
        data = experiment_context(state)
        data["command"] = shlex.join(
            ["hiraku", "run", "--record", record.id, "--", *command]
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
        if event != "launch_plan":
            return Command(
                update={"feedback": "Provide a launch plan."},
                goto="launch",
            )
        proposed_command = arguments["command"]
        wrapper = ["hiraku", "run", "--record", record.id, "--"]
        try:
            tokens = shlex.split(proposed_command)
        except ValueError as exc:
            return Command(
                update={"feedback": f"The launch command is invalid: {exc}"},
                goto="launch",
            )
        if tokens[: len(wrapper)] != wrapper or len(tokens) <= len(wrapper):
            return Command(
                update={
                    "feedback": (
                        "Use the `hiraku run --record <experiment> -- <argv...>` "
                        "form shown in the launch proposal."
                    )
                },
                goto="launch",
            )
        try:
            command = validate_command(tokens[len(wrapper) :])
            if command is None:
                raise ValidationError("The experiment command cannot be empty")
            if command != getattr(record, "command", None):
                record.command = command
                record.save()
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="launch")
        approved_command = shlex.join([*wrapper, *command])
        if approved_command != proposed_command:
            return Command(
                update={
                    "feedback": (
                        "Resubmit the launch command using the exact shell quoting "
                        f"shown here: {approved_command}"
                    )
                },
                goto="launch",
            )
        revision = sha256(approved_command.encode("utf-8")).hexdigest()
        return Command(
            update={
                "proposed_launch_command": approved_command,
                "proposed_launch_revision": revision,
                "feedback": "",
            },
            goto="launch_approval",
        )

    def launch_approval(
        state: ResearchState,
    ) -> Command[Literal["launch", "launch_execution", "done"]]:
        data = {
            **experiment_context(state),
            "command": state.get("proposed_launch_command", ""),
            "plan_revision": state.get("proposed_launch_revision", ""),
        }
        decision, feedback = decision_for(
            state,
            "launch",
            data,
            expected_event="launch_approved",
            revision=state.get("proposed_launch_revision", ""),
        )
        if decision == "stop":
            return finish(state)
        if decision == "revise":
            return Command(
                update={"feedback": feedback or "Revise the launch plan."},
                goto="launch",
            )
        return Command(
            update={
                "approved_launch_command": state.get("proposed_launch_command", ""),
                "feedback": "",
            },
            goto="launch_execution",
        )

    def launch_execution(
        state: ResearchState,
    ) -> Command[Literal["launch", "analyze", "done"]]:
        record = experiment(state)
        data = experiment_context(state)
        data["command"] = state.get("approved_launch_command", "")
        response = execution_response_for(state, "launch", data)
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
        run_id = arguments["run_id"]
        matching_run = next(
            (
                run
                for run in _runs(record, project)
                if run.id == run_id and run.commit == record.commit
            ),
            None,
        )
        if matching_run is None:
            return Command(
                update={
                    "feedback": (
                        f"Run {run_id!r} is not attributed to {record.id!r} on "
                        "the implemented commit. Check the run ID and try again."
                    )
                },
                goto="launch_execution",
            )
        record.status = "launched"
        record.save()
        return Command(
            update={
                "approved_launch_command": "",
                "proposed_launch_command": "",
                "proposed_launch_revision": "",
                "approved_plan": "",
                "proposed_plan": "",
                "proposed_plan_revision": "",
                "launched_run_id": matching_run.id,
                "feedback": "",
                "step": "analyze",
            },
            goto="analyze",
        )

    def analyze(
        state: ResearchState,
    ) -> Command[Literal["analyze", "next_iteration", "done"]]:
        record = experiment(state)
        launched_run_id = state.get("launched_run_id")
        runs = [run for run in _runs(record, project) if run.id == launched_run_id]
        data = {
            **experiment_context(state),
            "runs": [{"run_id": run.id, "metrics": dict(run.metrics)} for run in runs],
            "previous": state.get("results", []),
        }
        if not any(run.metrics for run in runs):
            data["metrics_pending"] = True
            state = {**state, "feedback": "The attributed runs have no metrics yet."}
        response = response_for(state, "analyze", data)
        event, arguments = response["event"], response["arguments"]
        if event == "finish":
            return finish(state)
        if not any(run.metrics for run in runs) and event == "analyzed":
            return Command(
                update={"feedback": "The attributed runs have no metrics yet."},
                goto="analyze",
            )
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
        return Command(
            update={
                "feedback": "",
                "step": "results_review",
            },
            goto="results_review",
        )

    def results_review(
        state: ResearchState,
    ) -> Command[Literal["analyze", "draft", "done"]]:
        record = experiment(state)
        result = {
            "record_id": record.id,
            "verdict": getattr(record, "verdict", "inconclusive"),
            "conclusion": getattr(record, "conclusion", ""),
        }
        decision, feedback = decision_for(
            state,
            "results_review",
            {
                "task": state["task"],
                "latest_result": result,
                "results": [*state.get("results", []), result],
                "remaining_rounds": state["rounds"] - len(state.get("results", [])),
            },
            interaction_type="review",
        )
        if decision == "revise":
            return Command(
                update={
                    "feedback": feedback or "Revise the analysis.",
                },
                goto="analyze",
            )
        results = [*state.get("results", []), result]
        update = {
            "results": results,
            "experiment": "",
            "launched_run_id": "",
            "feedback": "",
        }
        if decision == "stop" or len(results) >= state["rounds"]:
            return Command(update={**update, "step": "done"}, goto="done")
        return Command(
            update={**update, "step": "draft"},
            goto="draft",
        )

    def next_iteration(
        state: ResearchState,
    ) -> Command[Literal["draft", "done"]]:
        """Resume the pre-review-gate node for already-persisted sessions."""
        decision, feedback = decision_for(
            state,
            "next_iteration",
            {
                "task": state["task"],
                "results": state.get("results", []),
                "remaining_rounds": state["rounds"] - len(state.get("results", [])),
            },
        )
        if decision == "stop":
            return finish(state)
        return Command(
            update={
                "feedback": feedback or "" if decision == "revise" else "",
                "step": "draft",
            },
            goto="draft",
        )

    def done(state: ResearchState) -> dict[str, str]:
        return {
            "final": render(
                markdown.section("## Resume and completion"),
                {"results": state.get("results", [])},
            )
        }

    builder = StateGraph(ResearchState)
    builder.add_node("initialize", initialize)
    builder.add_node("draft", draft)
    builder.add_node("draft_approval", draft_approval)
    builder.add_node("implement", implement)
    builder.add_node("implement_approval", implement_approval)
    builder.add_node("implement_execution", implement_execution)
    builder.add_node("launch", launch)
    builder.add_node("launch_approval", launch_approval)
    builder.add_node("launch_execution", launch_execution)
    builder.add_node("analyze", analyze)
    builder.add_node("results_review", results_review)
    builder.add_node("next_iteration", next_iteration)
    builder.add_node("done", done)
    builder.add_edge(START, "initialize")
    builder.add_edge("done", END)
    return builder.compile(checkpointer=checkpointer)


def _runs(record: Record, project: Project) -> list[Run]:
    return record.runs(project=project)


def _step_response_contract(
    step: str, phase: Literal["review", "execution"]
) -> tuple[TypeAdapter[Any], dict[str, Any]]:
    arguments_by_event: dict[str, type[BaseModel]] = {
        "drafted": DraftedArguments,
        "implementation_plan": ImplementationPlanArguments,
        "implementation_approved": PlanRevisionArguments,
        "implemented": ImplementedArguments,
        "analyzed": AnalyzedArguments,
        "revise_draft": FeedbackArguments,
        "revise_implementation": FeedbackArguments,
        "revise_launch": FeedbackArguments,
        "revise_analysis": FeedbackArguments,
        "launched": LaunchedArguments,
        "launch_plan": LaunchPlanArguments,
        "launch_approved": PlanRevisionArguments,
        "finish": EmptyArguments,
    }
    proposal_events = {
        "draft": "drafted",
        "implement": "implementation_plan",
        "launch": "launch_plan",
        "analyze": "analyzed",
    }
    result_events = {"implement": "implemented", "launch": "launched"}
    revise_events = {
        "draft": "revise_draft",
        "implement": "revise_implementation",
        "launch": "revise_launch",
        "analyze": "revise_analysis",
    }
    if phase == "review":
        events = (
            (proposal_events[step], revise_events[step])
            if step == "analyze"
            else (proposal_events[step], revise_events[step], "finish")
        )
    else:
        result_event = result_events.get(step)
        if result_event is None:
            raise ValueError(f"Step {step!r} has no separate execution phase")
        events = (result_event, revise_events[step], "finish")
    event_models = []
    for event in events:
        arguments_model = arguments_by_event[event]
        fields: dict[str, Any] = {"event": (Literal[event], ...)}
        if arguments_model is EmptyArguments:
            fields["arguments"] = (
                arguments_model,
                Field(default_factory=arguments_model),
            )
        else:
            fields["arguments"] = (arguments_model, ...)
        event_models.append(
            create_model(
                f"{step.title()}{event.title().replace('_', '')}Response",
                __config__=ConfigDict(extra="forbid", strict=True),
                **fields,
            )
        )
    action_type = Annotated[reduce(or_, event_models), Field(discriminator="event")]
    action_adapter = TypeAdapter(action_type)
    return action_adapter, action_adapter.json_schema()


def _approval_response_schema(
    expected_event: str | None, revision: str | None
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "decision": (Literal["approve", "revise", "stop"], ...),
    }
    if expected_event is not None:
        if revision is None:
            raise ValueError("An approval revision is required for a protected plan")
        fields["revision"] = (Literal[revision], ...)
    approval_model = create_model(
        "WorkflowApprovalResponse",
        __config__=ConfigDict(extra="forbid", strict=True),
        **fields,
    )
    envelope_fields: dict[str, Any] = {"approval": (approval_model, ...)}
    return create_model(
        "WorkflowApprovalEnvelope",
        __config__=ConfigDict(extra="forbid", strict=True),
        **envelope_fields,
    ).model_json_schema()
