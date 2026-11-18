"""Checkpointed research workflow over Hiraku task and experiment records."""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

import frontmatter
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import HirakuError, ValidationError
from hiraku.git import GitRepository
from hiraku.job import validate_command
from hiraku.markdown import Markdown
from hiraku.project import Project
from hiraku.record import Record
from hiraku.tracking import Run
from hiraku.tracking.leaderboard import Leaderboard

Verdict = Literal["supports", "refutes", "inconclusive"]
TriageDecision = Literal["follow-up", "stop"]
# Compute comes first, so jobs run while the agent analyzes and designs; reviews
# come right before the step they unblock.
WORK_PRIORITIES = (
    "launch",
    "review_code",
    "review_design",
    "implement",
    "review_result",
    "triage",
    "analyze",
    "design",
)
APPROVAL = (
    "Present this to the user and wait for their approval before you send the "
    "response; apply their changes first."
)
AUTONOMOUS = "Decide on your own; do not wait for the user."
REVIEW_CAPABILITIES = ["record.read", "filesystem.read", "web.search"]
# Keep the end of long smoke-test output.
SMOKE_OUTPUT_LIMIT = 4000
Sources = Annotated[
    list[str],
    Field(
        min_length=1,
        description="Where the judgement comes from: URLs, papers, or "
        "'model knowledge'.",
    ),
]


class ResearchState(TypedDict, total=False):
    arguments: dict[str, Any]
    run_id: str
    task: str
    command: list[str]
    max_experiments: int
    max_depth: int
    max_repairs: int
    autonomous: bool
    review_rounds: int
    current: str
    # The commit under code review; a review must not change it.
    review_commit: str
    # Feedback of rejected design reviews and their count, by parent record.
    design_feedback: dict[str, str]
    design_rounds: dict[str, int]
    step: str
    feedback: str
    final: str


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: str | None = Field(
        default=None,
        min_length=1,
        description="Task record ID; defaults to the active task when available.",
    )
    command: list[str] = Field(
        min_length=1, description="Command to run an experiment."
    )
    max_experiments: int = Field(default=6, gt=0, le=50)
    max_depth: int = Field(default=3, gt=0, le=10)
    max_repairs: int = Field(default=2, ge=0, le=5)
    autonomous: bool = Field(
        default=False,
        description="No person approves anything; independent reviews of the "
        "design, the code, and the result take that role.",
    )
    review_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Reviews that may send an experiment back before it is kept "
        "as unresolved or concluded as inconclusive.",
    )


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    change: str = Field(min_length=1)


class DesignedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    experiments: list[Plan] = Field(min_length=1)


class CommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    commit: str = Field(min_length=1)
    smoke_command: str = Field(default="", description="The smoke test you ran.")
    smoke_output: str = Field(default="", description="Its output, or the end of it.")


class DesignReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "revise"]
    findings: str = Field(min_length=1)
    sources: Sources


class CodeReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "fix"]
    findings: str = Field(min_length=1)


class ResultReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    own_verdict: Verdict = Field(
        description="Your verdict from the runs, formed before reading the analysis."
    )
    decision: Literal["approve", "reanalyze", "fix_code"]
    findings: str = Field(min_length=1)
    sources: Sources


class LaunchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: Literal["launched", "failed"]
    record_id: str | None = None
    reason: str | None = None


class WaitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: Literal["checked", "failed"]
    record_id: str | None = None
    reason: str | None = None


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Verdict
    conclusion: str = Field(min_length=1)


class TriageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: TriageDecision
    reason: str = Field(min_length=1)


class SetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: Literal["set_up"]


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str = Field(min_length=1)


def input_schema() -> dict[str, Any]:
    """Return the JSON Schema accepted when starting a research session."""
    return ResearchInput.model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, project: Project) -> Any:
    """Build the graph that guides setup, design, execution, and analysis."""
    path = Path(__file__).with_name("workflow.md")
    post = frontmatter.load(path)
    if post.metadata.get("id") != "research":
        raise ValueError("workflow.md must declare id: research")
    markdown = Markdown(post.content)

    def initialize(state: ResearchState) -> Command[Literal["select"]]:
        try:
            arguments = ResearchInput.model_validate(state.get("arguments", {}))
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        task = arguments.task
        if task is None:
            active = project.active_record_id
            if active:
                try:
                    active_record = project.vault.load(active)
                except HirakuError:
                    active_record = None
                if active_record is not None and active_record.type == "task":
                    task = active
        if task:
            try:
                record = project.vault.load(task)
            except HirakuError as exc:
                raise ValidationError(str(exc)) from exc
            if record.type != "task":
                raise ValidationError(f"Research root {task!r} must be a task record")
            task = record.id
        return Command(
            update={
                "task": task or "",
                "command": validate_command(arguments.command),
                "max_experiments": arguments.max_experiments,
                "max_depth": arguments.max_depth,
                "max_repairs": arguments.max_repairs,
                "autonomous": arguments.autonomous,
                "review_rounds": arguments.review_rounds,
                "design_feedback": {},
                "design_rounds": {},
                "feedback": "",
            },
            goto="select",
        )

    def select(
        state: ResearchState,
    ) -> Command[
        Literal[
            "setup",
            "draft",
            "design",
            "review_design",
            "implement",
            "review_code",
            "launch",
            "wait",
            "analyze",
            "review_result",
            "triage",
            "done",
        ]
    ]:
        problems = _setup_problems(project)
        if problems:
            return Command(update={"step": "setup"}, goto="setup")
        task_id = state.get("task") or ""
        task_update: dict[str, Any] = {}
        if not task_id:
            active = project.active_record_id
            if active:
                with _ignore_hiraku_errors():
                    record = project.vault.load(active)
                    if record.type == "task":
                        task_id = active
            if not task_id:
                return Command(update={"task": "", "step": "draft"}, goto="draft")
            task_update["task"] = task_id

        _task(task_id, project)
        tree = _tree(task_id, project, state["max_depth"], _autonomous(state))
        if len(tree) > state["max_experiments"]:
            # Existing studies are never hidden or truncated by a new session.
            remaining = 0
        else:
            remaining = state["max_experiments"] - len(tree)

        candidates: list[tuple[str, str]] = []
        waiting = False
        children = _children(project)
        for experiment, depth in _ordered(task_id, children):
            step = _step_of(experiment, depth, state, children, project)
            if step == "waiting":
                waiting = True
            elif step in WORK_PRIORITIES and (step != "design" or remaining > 0):
                candidates.append((step, experiment.id))
        if not tree and remaining > 0:
            candidates.append(("design", task_id))
        selected = min(
            candidates,
            key=lambda item: (
                WORK_PRIORITIES.index(item[0]),
                _tree_order(item[1], task_id, children),
            ),
            default=None,
        )
        if selected:
            step, current = selected
            update: dict[str, Any] = {**task_update, "step": step, "current": current}
            if step == "review_code":
                update["review_commit"] = project.vault.load(current).commit or ""
            return Command(update=update, goto=step)
        if waiting:
            return Command(
                update={**task_update, "step": "wait", "current": ""}, goto="wait"
            )
        return Command(
            update={**task_update, "step": "done", "current": ""}, goto="done"
        )

    def setup(state: ResearchState) -> Command[Literal["setup", "select", "done"]]:
        data = {
            "problems": _setup_problems(project),
            "slurm": shutil.which("sbatch") is not None,
            "job_scripts": _job_scripts(project.root),
            "leaderboards": Leaderboard.list(project),
        }
        _ask(
            markdown,
            "Setup",
            data,
            SetupResponse,
            state.get("feedback", ""),
            ["filesystem.read", "filesystem.write", "workflow.user_input"],
            approval=_approval(state),
        )
        problems = _setup_problems(project)
        if problems:
            return Command(
                update={"feedback": "Still to fix: " + "; ".join(problems)},
                goto="setup",
            )
        return Command(update={"feedback": ""}, goto="select")

    def draft(state: ResearchState) -> Command[Literal["draft", "select"]]:
        data = {"command": state["command"]}
        response = _ask(
            markdown,
            "Draft",
            data,
            DraftResponse,
            state.get("feedback", ""),
            ["record.read", "record.create", "workflow.user_input"],
        )
        try:
            task = _task(response.record_id, project)
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="draft")
        return Command(update={"task": task.id, "feedback": ""}, goto="select")

    def design(state: ResearchState) -> Command[Literal["design", "select"]]:
        parent = project.vault.load(state["current"])
        tree = _tree(state["task"], project, state["max_depth"], _autonomous(state))
        remaining = max(state["max_experiments"] - len(tree), 0)
        depth = _record_depth(parent, state["task"], project)
        data = {
            "task": state["task"],
            "parent": _describe(parent),
            "depth": depth + 1,
            "remaining": remaining,
            "tree": tree,
            "review_feedback": state.get("design_feedback", {}).get(parent.id),
        }
        response = _ask(
            markdown,
            "Design",
            data,
            DesignedResponse,
            state.get("feedback", ""),
            ["record.read", "record.create", "workflow.user_input"],
            approval=_approval(state),
        )
        if len(response.experiments) > remaining:
            return Command(
                update={"feedback": f"Propose at most {remaining} experiments."},
                goto="design",
            )
        if depth >= state["max_depth"]:
            return Command(
                update={"feedback": f"The maximum depth is {state['max_depth']}."},
                goto="design",
            )
        known = {record.id for record in project.vault.list()}
        identifiers = [plan.record_id for plan in response.experiments]
        if len(set(identifiers)) != len(identifiers) or known.intersection(identifiers):
            return Command(
                update={"feedback": "Experiment record IDs must be new and distinct."},
                goto="design",
            )
        existing_tree = _tree(
            state["task"], project, state["max_depth"], _autonomous(state)
        )
        sequence = max((int(item["sequence"]) for item in existing_tree), default=0)
        try:
            for index, plan in enumerate(response.experiments, start=1):
                project.vault.create(
                    plan.record_id,
                    parent_id=parent.id,
                    type="experiment",
                    state="designed",
                    summary=plan.summary,
                    hypothesis=plan.hypothesis,
                    expected=plan.expected,
                    sequence=sequence + index,
                    markdown=f"## Change\n\n{plan.change.strip()}\n",
                )
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="design")
        return Command(update={"feedback": ""}, goto="select")

    def implement(state: ResearchState) -> Command[Literal["implement", "select"]]:
        experiment = project.vault.load(state["current"])
        parent = project.vault.load(str(experiment.parent_id))
        data = {
            "task": state["task"],
            "experiment": _describe(experiment),
            "parent_commit": parent.commit if parent.type == "experiment" else None,
            "command": state["command"],
            "failure": getattr(experiment, "failure", None),
            "review_feedback": getattr(experiment, "review_feedback", None),
            "smoke_test_required": _autonomous(state),
        }
        response = _ask(
            markdown,
            "Implement",
            data,
            CommitResponse,
            state.get("feedback", ""),
            ["filesystem.read", "filesystem.write", "shell.execute", "git.commit"],
        )
        if _autonomous(state) and not (
            response.smoke_command.strip() and response.smoke_output.strip()
        ):
            return Command(
                update={
                    "feedback": "Run a smoke test of the change and send its "
                    "command and output."
                },
                goto="implement",
            )
        try:
            sha = GitRepository(project.root).resolve(response.commit)
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="implement")
        experiment.commit = sha
        experiment.state = "implemented"
        experiment.failure = None
        experiment.smoke_command = response.smoke_command or None
        experiment.smoke_output = response.smoke_output[-SMOKE_OUTPUT_LIMIT:] or None
        experiment.review_feedback = None
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def launch(state: ResearchState) -> Command[Literal["launch", "select"]]:
        experiment = project.vault.load(state["current"])
        command = state["command"]
        rendered = shlex.join(
            ["hiraku", "run", "--record", experiment.id, "--", *command]
        )
        response = _ask(
            markdown,
            "Launch",
            {"experiment": _describe(experiment), "command": rendered},
            LaunchResponse,
            state.get("feedback", ""),
            ["experiment.launch", "tracking.run.read"],
        )
        if response.event == "failed":
            if response.record_id not in (None, experiment.id) or not response.reason:
                return Command(
                    update={
                        "feedback": "Report this experiment ID and the failure reason."
                    },
                    goto="launch",
                )
            _failed(experiment.id, response.reason, state, project)
            return Command(update={"feedback": ""}, goto="select")
        experiment.state = "launched"
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def wait(state: ResearchState) -> Command[Literal["wait", "select"]]:
        waiting = [
            item
            for item in _tree(
                state["task"], project, state["max_depth"], _autonomous(state)
            )
            if item["step"] == "waiting"
        ]
        response = _ask(
            markdown,
            "Wait",
            {"launched": waiting},
            WaitResponse,
            state.get("feedback", ""),
            ["tracking.run.read", "workflow.user_input"],
        )
        if response.event == "failed":
            waiting_ids = {item["id"] for item in waiting}
            if response.record_id not in waiting_ids or not response.reason:
                return Command(
                    update={
                        "feedback": (
                            "Report a waiting experiment ID and the failure reason."
                        )
                    },
                    goto="wait",
                )
            _failed(response.record_id, response.reason, state, project)
            return Command(update={"feedback": ""}, goto="select")
        fresh = _tree(state["task"], project, state["max_depth"], _autonomous(state))
        if any(item["step"] == "waiting" for item in fresh) and not any(
            item["step"] in WORK_PRIORITIES for item in fresh
        ):
            return Command(
                update={
                    "feedback": (
                        "No launched experiment has a run with metrics yet; "
                        "check again later."
                    )
                },
                goto="wait",
            )
        return Command(update={"feedback": ""}, goto="select")

    def analyze(state: ResearchState) -> Command[Literal["analyze", "select"]]:
        experiment = project.vault.load(state["current"])
        parent = project.vault.load(str(experiment.parent_id))
        task = _task(state["task"], project)
        runs = _matching_runs(experiment, project)
        usable = [run for run in runs if run.metrics]
        data = {
            "experiment": _describe(experiment),
            "parent": _describe(parent) if parent.type == "experiment" else None,
            "metrics": _metrics(task),
            "runs": [
                {
                    "run_id": run.id,
                    "metrics": dict(run.metrics),
                    "params": dict(run.params),
                }
                for run in usable
            ],
            "review_feedback": getattr(experiment, "review_feedback", None),
        }
        response = _ask(
            markdown,
            "Analyze",
            data,
            AnalysisResponse,
            state.get("feedback", ""),
            ["record.read", "tracking.run.read", "workflow.user_input"],
        )
        if not usable:
            return Command(
                update={"feedback": "No attributed run with metrics is available yet."},
                goto="analyze",
            )
        experiment.state = "analyzed"
        experiment.verdict = response.verdict
        experiment.conclusion = response.conclusion
        experiment.result_review = None
        experiment.review_feedback = None
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def review_design(
        state: ResearchState,
    ) -> Command[Literal["review_design", "select"]]:
        batch = _batch(state, project)
        parent = project.vault.load(str(batch[0].parent_id))
        task = _task(state["task"], project)
        data = {
            "task": _describe(task),
            "metrics": _metrics(task),
            "parent": _describe(parent),
            "experiments": [_describe(item) for item in batch],
            "tree": _tree(state["task"], project, state["max_depth"], True),
        }
        response = _ask(
            markdown,
            "Review design",
            data,
            DesignReviewResponse,
            state.get("feedback", ""),
            REVIEW_CAPABILITIES,
        )
        rounds = state.get("design_rounds", {}).get(parent.id, 0) + 1
        if response.decision == "revise" and rounds <= state["review_rounds"]:
            # The experiments are not implemented yet: drop them and redesign.
            for item in batch:
                project.vault.delete(item.id)
            return Command(
                update={
                    "design_rounds": {
                        **state.get("design_rounds", {}),
                        parent.id: rounds,
                    },
                    "design_feedback": {
                        **state.get("design_feedback", {}),
                        parent.id: response.findings,
                    },
                    "feedback": "",
                },
                goto="select",
            )
        outcome = "approved" if response.decision == "approve" else "unresolved"
        for item in batch:
            item.design_review = outcome
            _log_review(
                item,
                {
                    "kind": "design",
                    "decision": response.decision,
                    "findings": response.findings,
                    "sources": response.sources,
                },
            )
            project.vault.save(item, overwrite=True)
        design_feedback = dict(state.get("design_feedback", {}))
        design_feedback.pop(parent.id, None)
        return Command(
            update={"design_feedback": design_feedback, "feedback": ""}, goto="select"
        )

    def review_code(
        state: ResearchState,
    ) -> Command[Literal["review_code", "select"]]:
        experiment = project.vault.load(state["current"])
        parent = project.vault.load(str(experiment.parent_id))
        base = parent.commit if parent.type == "experiment" else None
        commit = str(experiment.commit)
        data = {
            "experiment": _describe(experiment),
            "command": state["command"],
            "diff": f"git diff {base or commit + '^'} {commit}",
            "smoke_test": {
                "command": getattr(experiment, "smoke_command", None),
                "output": getattr(experiment, "smoke_output", None),
            },
        }
        response = _ask(
            markdown,
            "Review code",
            data,
            CodeReviewResponse,
            state.get("feedback", ""),
            [*REVIEW_CAPABILITIES, "shell.execute"],
        )
        experiment = project.vault.load(state["current"])
        if experiment.commit != state.get("review_commit"):
            return Command(
                update={
                    "feedback": "The experiment's commit changed during the review; "
                    "a review must not change code."
                },
                goto="review_code",
            )
        _log_review(
            experiment,
            {
                "kind": "code",
                "decision": response.decision,
                "findings": response.findings,
            },
        )
        if response.decision == "approve":
            experiment.code_review = experiment.commit
        else:
            _send_back(
                experiment,
                "code",
                response.findings,
                to="implement",
                rounds=state["review_rounds"],
            )
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def review_result(
        state: ResearchState,
    ) -> Command[Literal["review_result", "select"]]:
        experiment = project.vault.load(state["current"])
        parent = project.vault.load(str(experiment.parent_id))
        task = _task(state["task"], project)
        plan = _describe(experiment)
        analysis = {
            "verdict": plan.pop("verdict", None),
            "conclusion": plan.pop("conclusion", None),
        }
        data = {
            "experiment": plan,
            "parent": _describe(parent) if parent.type == "experiment" else None,
            "metrics": _metrics(task),
            "runs": [
                {
                    "run_id": run.id,
                    "metrics": dict(run.metrics),
                    "params": dict(run.params),
                }
                for run in _matching_runs(experiment, project)
            ],
            "analysis": analysis,
        }
        response = _ask(
            markdown,
            "Review result",
            data,
            ResultReviewResponse,
            state.get("feedback", ""),
            REVIEW_CAPABILITIES,
        )
        _log_review(
            experiment,
            {
                "kind": "result",
                "decision": response.decision,
                "own_verdict": response.own_verdict,
                "findings": response.findings,
                "sources": response.sources,
            },
        )
        if response.decision == "approve":
            experiment.result_review = "approved"
        else:
            _send_back(
                experiment,
                "result",
                response.findings,
                to="analyze" if response.decision == "reanalyze" else "implement",
                rounds=state["review_rounds"],
            )
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def triage(state: ResearchState) -> Command[Literal["triage", "select"]]:
        experiment = project.vault.load(state["current"])
        tree = _tree(state["task"], project, state["max_depth"], _autonomous(state))
        depth = _record_depth(experiment, state["task"], project)
        remaining = max(state["max_experiments"] - len(tree), 0)
        can_follow_up = depth < state["max_depth"] and remaining > 0
        data = {
            "experiment": _describe(experiment),
            "depth": depth,
            "remaining": remaining,
            "can_follow_up": can_follow_up,
            "tree": tree,
        }
        response = _ask(
            markdown,
            "Triage",
            data,
            TriageResponse,
            state.get("feedback", ""),
            ["record.read", "workflow.user_input"],
            approval=_approval(state),
        )
        if response.decision == "follow-up" and not can_follow_up:
            reason = (
                f"Cannot follow up: maximum depth {state['max_depth']} or "
                f"experiment limit {state['max_experiments']} reached."
            )
            return Command(update={"feedback": reason}, goto="triage")
        experiment.triage = response.decision
        experiment.triage_reason = response.reason
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def done(state: ResearchState) -> dict[str, str]:
        tree = (
            _tree(state["task"], project, state["max_depth"], _autonomous(state))
            if state.get("task")
            else []
        )
        best = _best_results(state["task"], project) if state.get("task") else []
        return {
            "final": markdown.section("## Done").replace(
                "{{task}}", state.get("task", "")
            )
            + "\n\n"
            + _format_summary(tree, best)
        }

    builder = StateGraph(ResearchState)
    for name, function in (
        ("initialize", initialize),
        ("select", select),
        ("setup", setup),
        ("draft", draft),
        ("design", design),
        ("implement", implement),
        ("launch", launch),
        ("wait", wait),
        ("analyze", analyze),
        ("review_design", review_design),
        ("review_code", review_code),
        ("review_result", review_result),
        ("triage", triage),
        ("done", done),
    ):
        builder.add_node(name, function)
    builder.add_edge(START, "initialize")
    builder.add_edge("done", END)
    return builder.compile(checkpointer=checkpointer)


def _ask(
    markdown: Markdown,
    title: str,
    data: dict[str, Any],
    response_model: type[BaseModel],
    feedback: str,
    capabilities: list[str],
    approval: str = "",
) -> BaseModel:
    instruction = markdown.section(f"## {title}")
    if approval:
        instruction = f"{instruction}\n\n{approval}"
    payload: dict[str, Any] = {
        "step": title.lower().replace(" ", "_"),
        "instruction": instruction,
        "data": data,
        "response_schema": response_model.model_json_schema(),
        "required_capabilities": capabilities,
    }
    if feedback:
        payload["feedback"] = feedback
    while True:
        response = interrupt(payload)
        try:
            return response_model.model_validate(response)
        except PydanticValidationError as exc:
            payload = {**payload, "error": f"Response does not match the schema: {exc}"}


def _setup_problems(project: Project) -> list[str]:
    problems = []
    contexts = project.vault.list()
    if not any(
        item.id == "project-setup" and item.type == "context" for item in contexts
    ):
        problems.append(
            "no `project-setup` context says how experiments run in this project"
        )
    if not Leaderboard.list(project):
        problems.append(
            "no leaderboard defines where runs are tracked; "
            "create one with `hiraku leaderboard create`"
        )
    return problems


def _task(record_id: str, project: Project) -> Record:
    record = project.vault.load(record_id)
    if record.type != "task":
        raise ValidationError(f"Record {record_id!r} must have type: task")
    if not getattr(record, "metrics", None):
        raise ValidationError(f"Task {record_id!r} must declare at least one metric")
    return record


def _metrics(task: Record) -> list[dict[str, Any]]:
    return [dict(item) for item in getattr(task, "metrics", None) or []]


def _children(project: Project) -> dict[str, list[Record]]:
    result: dict[str, list[Record]] = {}
    for record in project.vault.list():
        if record.type == "experiment" and record.parent_id:
            result.setdefault(record.parent_id, []).append(record)
    for records in result.values():
        records.sort(key=lambda item: (int(getattr(item, "sequence", 0) or 0), item.id))
    return result


def _ordered(
    task_id: str, children: dict[str, list[Record]]
) -> Iterator[tuple[Record, int]]:
    level = children.get(task_id, [])
    depth = 1
    while level:
        following: list[Record] = []
        for record in level:
            yield record, depth
            following.extend(children.get(record.id, []))
        level, depth = following, depth + 1


def _autonomous(state: ResearchState) -> bool:
    return bool(state.get("autonomous", False))


def _approval(state: ResearchState) -> str:
    return AUTONOMOUS if _autonomous(state) else APPROVAL


def _batch(state: ResearchState, project: Project) -> list[Record]:
    """The experiments under review: the current one's unreviewed siblings."""
    current = project.vault.load(state["current"])
    siblings = _children(project).get(str(current.parent_id), [])
    return [
        item
        for item in siblings
        if item.commit is None and getattr(item, "design_review", None) is None
    ] or [current]


def _send_back(
    experiment: Record, kind: str, findings: str, *, to: str, rounds: int
) -> None:
    """Return an experiment to `to` with the findings, or conclude it."""
    field = f"{kind}_rounds"
    count = int(getattr(experiment, field, 0) or 0) + 1
    setattr(experiment, field, count)
    if count > rounds:
        experiment.state = "analyzed"
        experiment.verdict = "inconclusive"
        experiment.conclusion = (
            f"Did not pass the {kind} review after {rounds} rounds: {findings}"
        )
        experiment.result_review = "not needed"
        return
    experiment.review_feedback = findings
    if to == "analyze":
        experiment.verdict = None
        experiment.conclusion = None
        experiment.state = "launched"
        return
    experiment.commit = None
    experiment.code_review = None
    experiment.verdict = None
    experiment.conclusion = None
    experiment.result_review = None
    experiment.state = "designed"


def _log_review(experiment: Record, review: dict[str, Any]) -> None:
    experiment.reviews = [*(getattr(experiment, "reviews", None) or []), review]


def _step_of(
    experiment: Record,
    depth: int,
    state: ResearchState,
    children: dict[str, list[Record]],
    project: Project,
) -> str:
    reviewed = _autonomous(state)
    if getattr(experiment, "verdict", None):
        if getattr(experiment, "triage", None) is None:
            if (
                reviewed
                and experiment.state == "analyzed"
                and getattr(experiment, "result_review", None) is None
            ):
                return "review_result"
            return "triage"
        if (
            experiment.triage == "follow-up"
            and not children.get(experiment.id)
            and depth < state["max_depth"]
        ):
            return "design"
        return "done"
    if not experiment.commit:
        if reviewed and getattr(experiment, "design_review", None) is None:
            return "review_design"
        return "implement"
    if (
        reviewed
        and experiment.state == "implemented"
        and getattr(experiment, "code_review", None) != experiment.commit
    ):
        return "review_code"
    runs = _matching_runs(experiment, project)
    if any(run.metrics for run in runs):
        return "analyze"
    if runs or experiment.state == "launched":
        return "waiting"
    return "launch"


def _matching_runs(experiment: Record, project: Project) -> list[Run]:
    if not experiment.commit:
        return []
    if not Leaderboard.list(project):
        return []
    return [
        run
        for run in Leaderboard.load(project).list_runs(project, record_id=experiment.id)
        if run.commit == experiment.commit
    ]


def _tree(
    task_id: str,
    project: Project,
    max_depth: int = 10,
    autonomous: bool = False,
) -> list[dict[str, Any]]:
    children = _children(project)
    state: ResearchState = {
        "task": task_id,
        "max_depth": max_depth,
        "autonomous": autonomous,
    }
    rows = []
    for record, depth in _ordered(task_id, children):
        rows.append(
            {
                "id": record.id,
                "parent": record.parent_id,
                "depth": depth,
                "sequence": int(getattr(record, "sequence", 0) or 0),
                "step": _step_of(record, depth, state, children, project),
                "summary": getattr(record, "summary", None),
                "commit": record.commit,
                "verdict": getattr(record, "verdict", None),
                "triage": getattr(record, "triage", None),
                "conclusion": getattr(record, "conclusion", None),
                "reviews": getattr(record, "reviews", None) or [],
            }
        )
    return rows


def _tree_order(record_id: str, task_id: str, children: dict[str, list[Record]]) -> int:
    for index, (record, _depth) in enumerate(_ordered(task_id, children)):
        if record.id == record_id:
            return index
    return -1


def _record_depth(record: Record, task_id: str, project: Project) -> int:
    depth = 0
    while record.id != task_id:
        if not record.parent_id:
            raise ValidationError(f"{record.id!r} is not under task {task_id!r}")
        record = project.vault.load(record.parent_id)
        depth += 1
    return depth


def _describe(record: Record) -> dict[str, Any]:
    fields = record.model_dump(exclude={"path", "markdown"})
    fields["markdown"] = str(record.markdown)
    fields["record_id"] = record.id
    return fields


def _failed(
    record_id: str, reason: str, state: ResearchState, project: Project
) -> None:
    if not record_id:
        raise ValidationError("A failed event requires the experiment record ID")
    experiment = project.vault.load(record_id)
    repairs = int(getattr(experiment, "repairs", 0) or 0) + 1
    experiment.repairs = repairs
    experiment.failure = reason
    if repairs > state["max_repairs"]:
        experiment.state = "analyzed"
        experiment.verdict = "inconclusive"
        experiment.conclusion = (
            f"Could not be run after {state['max_repairs']} repairs: {reason}"
        )
        experiment.result_review = "not needed"
        experiment.triage = "stop"
    else:
        experiment.commit = None
        experiment.state = "designed"
    project.vault.save(experiment, overwrite=True)


def _best_results(task_id: str, project: Project) -> list[dict[str, Any]]:
    task = _task(task_id, project)
    results = []
    for metric in _metrics(task):
        candidates = []
        for item in _tree(task_id, project):
            record = project.vault.load(item["id"])
            for run in _matching_runs(record, project):
                value = run.metrics.get(metric["name"])
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                candidates.append((numeric, run, record))
        if not candidates:
            continue
        numeric, run, record = (
            min(candidates, key=lambda item: item[0])
            if metric["direction"] == "minimize"
            else max(candidates, key=lambda item: item[0])
        )
        results.append(
            {
                "record_id": record.id,
                "run_id": run.id,
                "metric": metric["name"],
                "value": numeric,
            }
        )
    return results


def _format_summary(tree: list[dict[str, Any]], best: list[dict[str, Any]]) -> str:
    lines = ["## Experiments", ""]
    if not tree:
        lines.append("No experiments were recorded.")
    for item in tree:
        lines.append(
            f"- `{item['id']}` (parent `{item['parent']}`): "
            f"{item['verdict'] or item['step']}; "
            f"{item.get('conclusion') or item.get('summary') or ''}"
        )
    lines.extend(["", "## Best result", ""])
    if best:
        lines.extend(
            f"`{item['metric']}` = {item['value']} in run `{item['run_id']}` "
            f"for experiment `{item['record_id']}`."
            for item in best
        )
    else:
        lines.append("No metric result is available yet.")
    open_items = [item["id"] for item in tree if item["step"] != "done"]
    lines.extend(["", "## Open questions", "", ", ".join(open_items) or "None."])
    return "\n".join(lines)


def _job_scripts(root: Path, limit: int = 20) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file() or ".git" in path.parts or ".hiraku" in path.parts:
            continue
        if "job" in path.name.lower() and path.suffix in {".sh", ".sbatch", ".slurm"}:
            found.append(str(path.relative_to(root)))
    return found


@contextmanager
def _ignore_hiraku_errors() -> Iterator[None]:
    try:
        yield
    except HirakuError:
        return
