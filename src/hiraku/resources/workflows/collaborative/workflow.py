"""
Compact, external-agent collaborative research workflow for Hiraku.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import HirakuError, ValidationError
from hiraku.execution import validate_command
from hiraku.git import GitRepository
from hiraku.markdown import Markdown, render
from hiraku.project import Project
from hiraku.record import Record, load_record

Verdict = Literal["supports", "refutes", "inconclusive"]


@dataclass(frozen=True)
class ResearchContext:
    project: Project


def build_context(project: Project) -> ResearchContext:
    return ResearchContext(project=project)


class ResearchState(TypedDict, total=False):
    arguments: dict[str, Any]
    task: str
    objective: str
    command: list[str]
    rounds: int
    experiment: str
    draft_hash: str
    plan: str
    plan_hash: str
    approved_plan: str
    launch_command: str
    launch_hash: str
    approved_command: str
    run_id: str
    feedback: str
    results: list[dict[str, Any]]
    final: str


class GraphInput(TypedDict):
    arguments: dict[str, Any]


class CollaborativeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: str | None = Field(
        default=None, description="Optional natural-language research objective."
    )
    record_id: str | None = Field(
        default=None,
        description="Parent task record ID; defaults to the active record.",
    )
    command: list[str] = Field(min_length=1)
    rounds: int = Field(default=3, ge=1)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    change: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: str = Field(min_length=1)


class LaunchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str = Field(min_length=1)


class Implementation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commit: str = Field(min_length=1)


class LaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: Verdict
    conclusion: str = Field(min_length=1)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "revise", "stop"]
    feedback: str | None = None
    revision: str | None = None

    @model_validator(mode="after")
    def check_feedback(self) -> Decision:
        if self.decision == "revise" and not (self.feedback or "").strip():
            raise ValueError("feedback is required when revising")
        if self.decision != "revise" and self.feedback is not None:
            raise ValueError("feedback is only allowed for revisions")
        return self


@dataclass(frozen=True)
class AgentTask:
    actor: Literal["agent"]
    action: str
    instruction: str
    data: dict[str, Any]
    response_fields: list[str]


@dataclass(frozen=True)
class HumanDecision:
    actor: Literal["human"]
    interaction: Literal["approval", "review"]
    action: str
    message: str
    proposal: dict[str, Any]
    revision: str | None = None


def input_schema() -> dict[str, Any]:
    return CollaborativeInput.model_json_schema()


def prepare_start_arguments(
    arguments: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Map the MCP session's selected record into this workflow's input."""
    record_id = context.get("record_id")
    if record_id is not None:
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValidationError("context.record_id must be a non-empty string")
        arguments.setdefault("record_id", record_id)
    return arguments


def _hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode()).hexdigest()


def _agent(
    action: str, instruction: str, data: dict[str, Any], model: type[BaseModel]
) -> BaseModel:
    """One interrupt per node, and no side effects before the interrupt."""
    fields = list(model.model_fields)
    task = AgentTask(
        actor="agent",
        action=action,
        instruction=(
            f"{instruction}\nSubmit one JSON object with these top-level fields: "
            f"{', '.join(fields)}."
        ),
        data=data,
        response_fields=fields,
    )
    raw = interrupt(asdict(task))
    return model.model_validate(raw)


def _decision(
    action: str,
    message: str,
    proposal: dict[str, Any],
    *,
    revision: str | None = None,
    interaction: Literal["approval", "review"] = "approval",
) -> Decision:
    raw = interrupt(
        asdict(HumanDecision("human", interaction, action, message, proposal, revision))
    )
    # The MCP integration must obtain this via a *trusted human* interaction
    # for interaction='approval'; a raw agent JSON response is insufficient.
    value = Decision.model_validate(raw)
    if revision is not None:
        # Revision is attached by the trusted server, not user-entered.
        if not isinstance(raw, dict) or raw.get("revision") != revision:
            raise ValidationError("Approval does not match proposal revision")
    elif value.revision is not None:
        raise ValidationError("Unexpected revision on an unbound workflow decision")
    return value


_AGENT_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "draft_experiment": Draft,
    "prepare_implementation_plan": Plan,
    "implement_experiment": Implementation,
    "prepare_launch": LaunchPlan,
    "launch_experiment": LaunchResult,
    "inspect_pending_metrics": Plan,
    "analyze_results": Analysis,
}


def validate_resume_response(pending: Any, response: dict[str, Any]) -> dict[str, Any]:
    """Validate input before Command(resume) can write it to a checkpoint."""
    if not isinstance(pending, dict):
        raise ValidationError("Pending collaborative interrupt must be an object")
    if not isinstance(response, dict):
        raise ValidationError("Workflow response must be an object")

    actor = pending.get("actor")
    if actor == "agent":
        action = pending.get("action")
        model = _AGENT_RESPONSE_MODELS.get(action)
        if model is None:
            raise ValidationError(f"Unknown collaborative agent action: {action!r}")
        try:
            validated = model.model_validate(response)
        except PydanticValidationError as exc:
            raise ValidationError(
                f"Invalid response for {action!r}; submit top-level fields "
                f"{list(model.model_fields)}: {exc}"
            ) from exc
        return validated.model_dump(mode="python")

    if actor == "human":
        interaction = pending.get("interaction")
        if interaction not in ("approval", "review"):
            raise ValidationError("Unsupported collaborative human interaction")
        try:
            decision = Decision.model_validate(response)
        except PydanticValidationError as exc:
            raise ValidationError(f"Invalid human workflow decision: {exc}") from exc
        revision = pending.get("revision")
        if interaction == "approval" and decision.revision != revision:
            raise ValidationError("Approval does not match proposal revision")
        if interaction == "review" and decision.revision is not None:
            raise ValidationError(
                "Review decisions cannot include an approval revision"
            )
        return decision.model_dump(mode="python", exclude_none=True)

    raise ValidationError("Unsupported collaborative interrupt owner")


def _record(state: ResearchState, runtime: Runtime[ResearchContext]) -> Record:
    record_id = state.get("experiment")
    if not record_id:
        raise ValidationError("No experiment has been drafted")
    return load_record(record_id, project=runtime.context.project)


def _experiment_data(
    state: ResearchState, runtime: Runtime[ResearchContext]
) -> dict[str, Any]:
    r = _record(state, runtime)
    return {
        "record_id": r.id,
        "summary": getattr(r, "summary", None),
        "hypothesis": getattr(r, "hypothesis", None),
        "change": r.markdown.strip(),
        "command": getattr(r, "command", None),
        "commit": r.commit,
    }


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    """Assemble the durable graph; supply ResearchContext at each invocation."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = Record._parse_markdown(path)
    if fields.get("id") != "collaborative":
        raise ValueError("workflow.md must declare id: collaborative")
    markdown = Markdown(body)
    rules = str(fields.get("rules", "")).strip()

    def instructions(section: str) -> str:
        text = markdown.section(section)
        return f"{text}\n\n{rules}" if rules else text

    def initialize(state: ResearchState, runtime: Runtime[ResearchContext]):
        args = CollaborativeInput.model_validate(state.get("arguments", {}))
        project = runtime.context.project
        objective = args.task
        task = args.record_id
        if task is None and args.task:
            # Preserve task-as-record callers while treating other task text as
            # guidance when a parent record is selected or active.
            try:
                load_record(args.task, project=project)
            except HirakuError:
                task = project.active_record_id
            else:
                task = args.task
                objective = ""
        if task is None:
            task = project.active_record_id
        if not task:
            raise ValidationError(
                "Select a parent record with context.record_id or activate one"
            )
        load_record(task, project=project)
        command = validate_command(args.command)
        return Command(
            update={
                "arguments": {},
                "task": task,
                "command": command,
                "objective": objective or "",
                "rounds": args.rounds,
                "results": [],
                "feedback": "",
            },
            goto="draft",
        )

    def draft(state: ResearchState, runtime: Runtime[ResearchContext]):
        data = {
            "task": state["task"],
            "objective": state.get("objective", ""),
            "suggested_command": state["command"],
            "previous_results": state.get("results", []),
            "feedback": state.get("feedback", ""),
        }
        if state.get("experiment"):
            data["existing_draft"] = _experiment_data(state, runtime)
        proposal = _agent(
            "draft_experiment", instructions("## Draft and review"), data, Draft
        )
        project = runtime.context.project
        command = validate_command(proposal.command)
        try:
            if state.get("experiment"):
                if proposal.record_id != state["experiment"]:
                    raise ValidationError(
                        "A revision must keep the experiment record ID"
                    )
                record = _record(state, runtime)
                record.summary = proposal.summary
                record.hypothesis = proposal.hypothesis
                record.command = command
                record.markdown = f"## Change\n\n{proposal.change.strip()}\n"
                record.save()
            else:
                record = Record.create(
                    proposal.record_id,
                    parent_id=state["task"],
                    type="experiment",
                    status="drafted",
                    summary=proposal.summary,
                    hypothesis=proposal.hypothesis,
                    command=command,
                    markdown=f"## Change\n\n{proposal.change.strip()}\n",
                    activate=False,
                    project=project,
                )
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="draft")
        digest = _hash(
            {
                "record_id": record.id,
                "summary": proposal.summary,
                "hypothesis": proposal.hypothesis,
                "change": proposal.change,
                "command": command,
            }
        )
        return Command(
            update={"experiment": record.id, "draft_hash": digest, "feedback": ""},
            goto="review_draft",
        )

    def review_draft(state: ResearchState, runtime: Runtime[ResearchContext]):
        decision = _decision(
            "approve_experiment_draft",
            "Approve this experiment draft?",
            _experiment_data(state, runtime),
            revision=state["draft_hash"],
        )
        if decision.decision == "stop":
            return Command(goto="done")
        if decision.decision == "revise":
            return Command(update={"feedback": decision.feedback or ""}, goto="draft")
        return Command(update={"feedback": ""}, goto="plan_implementation")

    def plan_implementation(state: ResearchState, runtime: Runtime[ResearchContext]):
        result = _agent(
            "prepare_implementation_plan",
            instructions("## Implementation"),
            {
                "experiment": _experiment_data(state, runtime),
                "feedback": state.get("feedback", ""),
            },
            Plan,
        )
        return Command(
            update={
                "plan": result.plan,
                "plan_hash": _hash(result.plan),
                "feedback": "",
            },
            goto="approve_implementation",
        )

    def approve_implementation(state: ResearchState, runtime: Runtime[ResearchContext]):
        decision = _decision(
            "approve_implementation",
            "Approve the implementation plan?",
            {"experiment": _experiment_data(state, runtime), "plan": state["plan"]},
            revision=state["plan_hash"],
        )
        if decision.decision == "stop":
            return Command(goto="done")
        if decision.decision == "revise":
            return Command(
                update={"feedback": decision.feedback or ""}, goto="plan_implementation"
            )
        return Command(
            update={"approved_plan": state["plan"], "feedback": ""}, goto="implement"
        )

    def implement(state: ResearchState, runtime: Runtime[ResearchContext]):
        result = _agent(
            "implement_experiment",
            (
                "Implement the approved plan, validate it, show changes, commit, "
                "and report commit SHA."
            ),
            {
                "experiment": _experiment_data(state, runtime),
                "approved_plan": state["approved_plan"],
            },
            Implementation,
        )
        project = runtime.context.project
        sha = GitRepository(project.root).resolve(result.commit)
        record = _record(state, runtime)
        record.commit = sha
        record.status = "implemented"
        record.save()
        return Command(update={"feedback": ""}, goto="plan_launch")

    def plan_launch(state: ResearchState, runtime: Runtime[ResearchContext]):
        record = _record(state, runtime)
        cmd = getattr(record, "command", None) or state["command"]
        suggested = shlex.join(["hiraku", "run", "--record", record.id, "--", *cmd])
        proposal = _agent(
            "prepare_launch",
            instructions("## Launch"),
            {
                "experiment": _experiment_data(state, runtime),
                "suggested_command": suggested,
                "feedback": state.get("feedback", ""),
            },
            LaunchPlan,
        )
        try:
            tokens = shlex.split(proposal.command)
        except ValueError as exc:
            return Command(update={"feedback": str(exc)}, goto="plan_launch")
        prefix = ["hiraku", "run", "--record", record.id, "--"]
        if tokens[: len(prefix)] != prefix or len(tokens) <= len(prefix):
            return Command(
                update={"feedback": "Use the hiraku run --record ... -- form"},
                goto="plan_launch",
            )
        command = validate_command(tokens[len(prefix) :])
        canonical = shlex.join([*prefix, *command])
        if canonical != proposal.command:
            return Command(
                update={"feedback": f"Use exact quoting: {canonical}"},
                goto="plan_launch",
            )
        return Command(
            update={
                "launch_command": canonical,
                "launch_hash": _hash(canonical),
                "feedback": "",
            },
            goto="approve_launch",
        )

    def approve_launch(state: ResearchState, runtime: Runtime[ResearchContext]):
        decision = _decision(
            "approve_launch",
            "Approve this exact experiment command?",
            {
                "experiment": _experiment_data(state, runtime),
                "command": state["launch_command"],
            },
            revision=state["launch_hash"],
        )
        if decision.decision == "stop":
            return Command(goto="done")
        if decision.decision == "revise":
            return Command(
                update={"feedback": decision.feedback or ""}, goto="plan_launch"
            )
        # Store only the approved command. Don't mutate the record command
        # until the approved transition succeeds.
        return Command(
            update={"approved_command": state["launch_command"]}, goto="launch"
        )

    def launch(state: ResearchState, runtime: Runtime[ResearchContext]):
        record = _record(state, runtime)
        result = _agent(
            "launch_experiment",
            "Check for an existing matching run before executing; otherwise run the "
            "approved command and report its run ID.",
            {
                "experiment": _experiment_data(state, runtime),
                "approved_command": state["approved_command"],
            },
            LaunchResult,
        )
        matching = next(
            (
                run
                for run in record.runs(project=runtime.context.project)
                if run.id == result.run_id and run.commit == record.commit
            ),
            None,
        )
        if matching is None:
            return Command(
                update={"feedback": "Run ID does not match the experiment commit"},
                goto="launch",
            )
        record.status = "launched"
        record.save()
        return Command(update={"run_id": matching.id, "feedback": ""}, goto="analyze")

    def analyze(state: ResearchState, runtime: Runtime[ResearchContext]):
        record = _record(state, runtime)
        runs = [
            run
            for run in record.runs(project=runtime.context.project)
            if run.id == state["run_id"]
        ]
        if not runs or not any(run.metrics for run in runs):
            # Return a useful agent handoff rather than inventing a verdict.
            result = _agent(
                "inspect_pending_metrics",
                "Metrics are unavailable. Investigate and report status.",
                {"run_id": state["run_id"]},
                Plan,
            )
            return Command(update={"feedback": result.plan}, goto="analyze")
        result = _agent(
            "analyze_results",
            instructions("## Analyze, review, and iterate"),
            {
                "experiment": _experiment_data(state, runtime),
                "runs": [
                    {"run_id": run.id, "metrics": dict(run.metrics)} for run in runs
                ],
                "previous": state.get("results", []),
                "feedback": state.get("feedback", ""),
            },
            Analysis,
        )
        record.status = "analyzed"
        record.verdict = result.verdict
        record.conclusion = result.conclusion
        record.save()
        return Command(update={"feedback": ""}, goto="review_results")

    def review_results(state: ResearchState, runtime: Runtime[ResearchContext]):
        record = _record(state, runtime)
        summary = {
            "record_id": record.id,
            "verdict": getattr(record, "verdict", None),
            "conclusion": getattr(record, "conclusion", None),
        }
        decision = _decision(
            "review_results",
            "Review the analysis and decide whether to iterate.",
            {
                "latest": summary,
                "previous": state.get("results", []),
                "remaining_rounds": state["rounds"] - len(state.get("results", [])),
            },
            interaction="review",
        )
        if decision.decision == "revise":
            return Command(update={"feedback": decision.feedback or ""}, goto="analyze")
        results = [*state.get("results", []), summary]
        if decision.decision == "stop" or len(results) >= state["rounds"]:
            return Command(update={"results": results}, goto="done")
        return Command(
            update={"results": results, "experiment": "", "run_id": "", "feedback": ""},
            goto="draft",
        )

    def done(state: ResearchState):
        return {
            "final": render(
                markdown.section("## Resume and completion"),
                {"results": state.get("results", [])},
            )
        }

    builder = StateGraph(
        ResearchState, context_schema=ResearchContext, input_schema=GraphInput
    )
    nodes = {
        "initialize": initialize,
        "draft": draft,
        "review_draft": review_draft,
        "plan_implementation": plan_implementation,
        "approve_implementation": approve_implementation,
        "implement": implement,
        "plan_launch": plan_launch,
        "approve_launch": approve_launch,
        "launch": launch,
        "analyze": analyze,
        "review_results": review_results,
        "done": done,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "initialize")
    builder.add_edge("done", END)
    return builder.compile(checkpointer=checkpointer)
