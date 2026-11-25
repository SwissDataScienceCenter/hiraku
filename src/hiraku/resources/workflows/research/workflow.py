"""Checkpointed research workflow over Hiraku task and experiment records."""

from __future__ import annotations

import hashlib
import json
import math
import shlex
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

import frontmatter
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import HirakuError, RecordNotFoundError, ValidationError
from hiraku.git import GitRepository
from hiraku.job import validate_command
from hiraku.markdown import Markdown
from hiraku.project import Project
from hiraku.record import Record
from hiraku.tracking import Run
from hiraku.tracking.leaderboard import Leaderboard

Verdict = Literal["supports", "refutes", "inconclusive"]
ReviewStage = Literal["design", "code", "result", "acceptance"]
Affects = Literal["analysis", "runs", "code", "design", "setup", "task", "unknown"]
TriageDecision = Literal["follow-up", "stop"]
# Compute comes first, so jobs run while the agent analyzes and designs; reviews
# come right before the step they unblock.
WORK_PRIORITIES = (
    "launch",
    "review_code",
    "review_design",
    "meta_review",
    "fix",
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
RESEARCHER = (
    "Ask the researcher this question and wait for their answer. Do not answer "
    "for them, even when the study is autonomous."
)
REVIEW_CAPABILITIES = [
    "record.read",
    "filesystem.read",
    "web.search",
    "tracking.run.read",
]
FIX_CAPABILITIES = [
    "record.read",
    "filesystem.read",
    "filesystem.write",
    "shell.execute",
    "git.commit",
    "experiment.launch",
    "tracking.run.read",
]
# The reviews on by default: the design, the result and the study as a whole.
# The code is checked by the smoke test and by the result review that follows,
# so its own review is opt-in.
DEFAULT_REVIEWS: tuple[ReviewStage, ...] = ("design", "result", "acceptance")
# Where each review kind keeps its approval; an acceptance reopen is a result fix.
STAGE_FIELDS = {
    "design": "design_review",
    "code": "code_review",
    "result": "result_review",
}
NOT_RUN = {
    "design": "Not run: design did not pass review.",
    "code": "Not run: code did not pass review.",
}
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
    reviews: list[str]
    review_rounds: int
    current: str
    acceptance_rounds: int
    # The commit under code review; a review must not change it.
    review_commit: str
    # Self-reported agent IDs and the role each one acted in.
    agents: dict[str, str]
    step: str
    feedback: str
    final: str


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: str | None = Field(
        default=None,
        min_length=1,
        description="Task record ID. Omit it to draft a new task with the user.",
    )
    command: list[str] = Field(
        min_length=1, description="Command to run an experiment."
    )
    max_experiments: int = Field(default=6, gt=0, le=50)
    max_depth: int = Field(default=3, gt=0, le=10)
    max_repairs: int = Field(default=2, ge=0, le=5)
    autonomous: bool = Field(
        default=False,
        description="No person approves anything; the independent reviews in "
        "`reviews` take that role.",
    )
    reviews: list[ReviewStage] = Field(
        default_factory=lambda: list(DEFAULT_REVIEWS),
        description="The independent reviews that run when `autonomous` is set: "
        "`design`, `code`, `result`, and `acceptance` (the summary and the review "
        "of the whole study). Each costs a fresh session per use.",
    )
    review_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Fix rounds a review may send an experiment back for before "
        "a meta review rules on the open findings.",
    )
    acceptance_rounds: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Times the acceptance review may reopen experiments.",
    )


def _agent() -> Any:
    return Field(
        default="",
        description="Your session or agent ID, if you have one. One ID cannot act "
        "in two roles of a study (author, a reviewer, meta reviewer).",
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
    agent: str = _agent()


class CommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    commit: str = Field(min_length=1)
    smoke_command: str = Field(default="", description="The smoke test you ran.")
    smoke_output: str = Field(default="", description="Its output, or the end of it.")
    agent: str = _agent()


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claim: str = Field(min_length=1, description="What is wrong.")
    evidence: str = Field(
        min_length=1, description="Why: run IDs with values, a diff line, a source."
    )
    severity: Literal["blocking", "minor"] = Field(
        default="blocking",
        description="Only blocking findings send the experiment back; minor ones "
        "are recorded as limitations.",
    )
    affects: Affects = Field(
        default="unknown",
        description="What the finding is about. `task` or `setup` is outside the "
        "experiment: the researcher decides.",
    )
    suggested_fix: str = ""
    unfixable: bool = Field(
        default=False,
        description="No fix to this experiment can resolve it, but it stays within "
        "the experiment's scope.",
    )
    continues: str = Field(
        default="",
        description="ID of an earlier finding that this one keeps open.",
    )


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float


class Judgement(BaseModel):
    """A decision with findings; `fix` needs a blocking finding."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "fix"]
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Judgement:
        blocking = any(item.severity == "blocking" for item in self.findings)
        if self.decision == "fix" and not blocking:
            raise ValueError("`fix` needs at least one blocking finding")
        if self.decision == "approve" and blocking:
            raise ValueError("`approve` cannot come with a blocking finding")
        return self


class DesignVerdict(Judgement):
    record_id: str = Field(min_length=1)


class DesignReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdicts: list[DesignVerdict] = Field(min_length=1)
    sources: Sources
    agent: str = _agent()


class CodeReviewResponse(Judgement):
    agent: str = _agent()


class ResultReviewResponse(Judgement):
    own_verdict: Verdict = Field(
        description="Your verdict from the runs, formed before reading the analysis."
    )
    sources: Sources
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="The run values your findings rest on; each is checked against "
        "the data.",
    )
    agent: str = _agent()


class Ruling(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    finding_id: str = Field(min_length=1)
    ruling: Literal["overrule", "limitation", "sustain"]
    reason: str = Field(min_length=1)


class MetaReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rulings: list[Ruling] = Field(min_length=1)
    sources: Sources
    evidence: list[Evidence] = Field(default_factory=list)
    agent: str = _agent()


class FindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    action: Literal["done", "dispute", "limitation"] = Field(
        description="`done`: what changed. `dispute`: your argument and evidence. "
        "`limitation`: accepted and recorded."
    )
    note: str = Field(min_length=1)


class ExcludedRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class FixResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    responses: list[FindingResponse] = Field(min_length=1)
    plan: Plan | None = Field(
        default=None, description="Design fix: the amended plan of this experiment."
    )
    commit: str = Field(default="", description="Code changed: the new commit.")
    smoke_command: str = ""
    smoke_output: str = ""
    verdict: Verdict | None = None
    conclusion: str | None = None
    excluded_runs: list[ExcludedRun] = Field(
        default_factory=list,
        description="Runs that are not valid evidence, each with a reason.",
    )
    agent: str = _agent()


class ResearcherResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: Literal["resolved", "accept_risk", "conclude"]
    note: str = ""


class SummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    report: str = Field(min_length=1)
    agent: str = _agent()


class Reopen(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    record_id: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)


class AcceptanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["accept", "accept_with_caveats", "reopen"]
    caveats: list[str] = Field(default_factory=list)
    reopen: list[Reopen] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: Sources
    agent: str = _agent()


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
    agent: str = _agent()


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
                "reviews": list(dict.fromkeys(arguments.reviews)),
                "review_rounds": arguments.review_rounds,
                "acceptance_rounds": arguments.acceptance_rounds,
                "agents": {},
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
            "fix",
            "meta_review",
            "researcher",
            "triage",
            "summarize",
            "accept",
            "done",
        ]
    ]:
        problems = _setup_problems(project)
        if problems:
            return Command(update={"step": "setup"}, goto="setup")
        task_id = state.get("task") or ""
        if not task_id:
            return Command(update={"task": "", "step": "draft"}, goto="draft")

        _task(task_id, project)
        tree = _tree(task_id, project, state["max_depth"], _reviews(state))
        if len(tree) > state["max_experiments"]:
            # Existing studies are never hidden or truncated by a new session.
            remaining = 0
        else:
            remaining = state["max_experiments"] - len(tree)

        candidates: list[tuple[str, str]] = []
        waiting = False
        asking: list[str] = []
        children = _children(project)
        for experiment, depth in _ordered(task_id, children):
            step = _step_of(experiment, depth, state, children, project)
            if step == "waiting":
                waiting = True
            elif step == "researcher":
                asking.append(experiment.id)
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
            update: dict[str, Any] = {"step": step, "current": current}
            if step == "review_code":
                update["review_commit"] = project.vault.load(current).commit or ""
            return Command(update=update, goto=step)
        if asking:
            # Only experiments that can make no progress wait for the researcher;
            # every other experiment ran first.
            return Command(
                update={"step": "researcher", "current": asking[0]}, goto="researcher"
            )
        if waiting:
            return Command(update={"step": "wait", "current": ""}, goto="wait")
        if tree and _review_on(state, "acceptance"):
            task = _task(task_id, project)
            if not getattr(task, "report", None):
                return Command(update={"step": "summarize"}, goto="summarize")
            if not getattr(task, "acceptance", None):
                return Command(update={"step": "accept"}, goto="accept")
        return Command(update={"step": "done", "current": ""}, goto="done")

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
        tree = _tree(state["task"], project, state["max_depth"], _reviews(state))
        remaining = max(state["max_experiments"] - len(tree), 0)
        depth = _record_depth(parent, state["task"], project)
        data = {
            "task": state["task"],
            "parent": _describe(parent),
            "depth": depth + 1,
            "remaining": remaining,
            "tree": tree,
        }
        response = _ask(
            markdown,
            "Design",
            data,
            DesignedResponse,
            state.get("feedback", ""),
            ["record.read", "record.create", "workflow.user_input"],
            approval=_approval(state),
            role="author",
            agents=state.get("agents"),
        )

        def retry(message: str) -> Command[Literal["design", "select"]]:
            return _next("design", state, "author", response, feedback=message)

        if len(response.experiments) > remaining:
            return retry(f"Propose at most {remaining} experiments.")
        if depth >= state["max_depth"]:
            return retry(f"The maximum depth is {state['max_depth']}.")
        known = {record.id for record in project.vault.list()}
        identifiers = [plan.record_id for plan in response.experiments]
        if len(set(identifiers)) != len(identifiers) or known.intersection(identifiers):
            return retry("Experiment record IDs must be new and distinct.")
        sequence = max((int(item["sequence"]) for item in tree), default=0)
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
                    **(
                        {"review_stages": _reviews(state)} if _autonomous(state) else {}
                    ),
                )
        except HirakuError as exc:
            return retry(str(exc))
        return _next("select", state, "author", response, feedback="")

    def implement(state: ResearchState) -> Command[Literal["implement", "select"]]:
        experiment = project.vault.load(state["current"])
        parent = project.vault.load(str(experiment.parent_id))
        data = {
            "task": state["task"],
            "experiment": _describe(experiment),
            "parent_commit": parent.commit if parent.type == "experiment" else None,
            "command": state["command"],
            "failure": getattr(experiment, "failure", None),
            "smoke_test_required": _autonomous(state),
            "project_setup": _project_setup(project),
        }
        response = _ask(
            markdown,
            "Implement",
            data,
            CommitResponse,
            state.get("feedback", ""),
            ["filesystem.read", "filesystem.write", "shell.execute", "git.commit"],
            role="author",
            agents=state.get("agents"),
        )

        def retry(message: str) -> Command[Literal["implement", "select"]]:
            return _next("implement", state, "author", response, feedback=message)

        if _autonomous(state) and not (
            response.smoke_command.strip() and response.smoke_output.strip()
        ):
            return retry(
                "Run a smoke test of the change and send its command and output."
            )
        try:
            sha = GitRepository(project.root).resolve(response.commit)
        except HirakuError as exc:
            return retry(str(exc))
        experiment.commit = sha
        experiment.state = "implemented"
        experiment.failure = None
        experiment.smoke_command = response.smoke_command or None
        experiment.smoke_output = response.smoke_output[-SMOKE_OUTPUT_LIMIT:] or None
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "author", response, feedback="")

    def launch(state: ResearchState) -> Command[Literal["launch", "select"]]:
        experiment = project.vault.load(state["current"])
        command = state["command"]
        rendered = shlex.join(
            ["hiraku", "run", "--record", experiment.id, "--", *command]
        )
        response = _ask(
            markdown,
            "Launch",
            {
                "experiment": _describe(experiment),
                "command": rendered,
                "project_setup": _project_setup(project),
            },
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
                state["task"], project, state["max_depth"], _reviews(state)
            )
            if item["step"] == "waiting"
        ]
        left_out = {
            item["id"]: stray
            for item in waiting
            if (stray := _left_out_runs(project.vault.load(item["id"]), project))
        }
        response = _ask(
            markdown,
            "Wait",
            {
                "launched": waiting,
                "runs_left_out": left_out,
                "project_setup": _project_setup(project),
            },
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
        fresh = _tree(state["task"], project, state["max_depth"], _reviews(state))
        if any(item["step"] == "waiting" for item in fresh) and not any(
            item["step"] in WORK_PRIORITIES for item in fresh
        ):
            stray = {
                item["id"]: _left_out_runs(project.vault.load(item["id"]), project)
                for item in fresh
                if item["step"] == "waiting"
            }
            # A run without a commit means the command does not record it; a run
            # with another commit belongs to an earlier commit of the experiment
            # (for example before a review fix) and only needs to be ignored.
            unrecorded = _runs_by_record(stray, recorded=False)
            earlier = _runs_by_record(stray, recorded=True)
            hint = ""
            if unrecorded:
                hint += (
                    " Runs with metrics but no commit were left out: "
                    + _list_runs(unrecorded)
                    + ". The command has to record the commit it runs at (for "
                    "W&B `hiraku.commit` in the run config); send `failed` for "
                    "the experiment so the code is fixed."
                )
            if earlier:
                hint += (
                    " Runs of an earlier commit were ignored: "
                    + _list_runs(earlier)
                    + "; wait for a run of the experiment's current commit."
                )
            return Command(
                update={
                    "feedback": (
                        "No launched experiment has a run with metrics yet; "
                        "check again later." + hint
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
        }
        response = _ask(
            markdown,
            "Analyze",
            data,
            AnalysisResponse,
            state.get("feedback", ""),
            ["record.read", "tracking.run.read", "workflow.user_input"],
            role="author",
            agents=state.get("agents"),
        )
        if not usable:
            return _next(
                "analyze",
                state,
                "author",
                response,
                feedback="No attributed run with metrics is available yet.",
            )
        experiment.state = "analyzed"
        experiment.verdict = response.verdict
        experiment.conclusion = response.conclusion
        experiment.result_review = None
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "author", response, feedback="")

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
            "tree": _tree(state["task"], project, state["max_depth"], _reviews(state)),
        }
        response = _ask(
            markdown,
            "Review design",
            data,
            DesignReviewResponse,
            state.get("feedback", ""),
            REVIEW_CAPABILITIES,
            role="design_review",
            agents=state.get("agents"),
        )
        wanted = sorted(item.id for item in batch)
        given = sorted(item.record_id for item in response.verdicts)
        if given != wanted:
            return _next(
                "review_design",
                state,
                "design_review",
                response,
                feedback="Give exactly one verdict for each experiment: "
                + ", ".join(wanted),
            )
        for verdict in response.verdicts:
            experiment = project.vault.load(verdict.record_id)
            _review(
                experiment,
                "design",
                verdict,
                state["review_rounds"],
                {"sources": response.sources, "agent": response.agent},
            )
            project.vault.save(experiment, overwrite=True)
        return _next("select", state, "design_review", response, feedback="")

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
            role="code_review",
            agents=state.get("agents"),
        )
        experiment = project.vault.load(state["current"])
        if experiment.commit != state.get("review_commit"):
            return _next(
                "review_code",
                state,
                "code_review",
                response,
                feedback="The experiment's commit changed during the review; "
                "a review must not change code.",
            )
        _review(
            experiment,
            "code",
            response,
            state["review_rounds"],
            {"agent": response.agent},
        )
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "code_review", response, feedback="")

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
        evidence = _evidence(experiment, project)
        data = {
            "experiment": plan,
            "parent": _describe(parent) if parent.type == "experiment" else None,
            "metrics": _metrics(task),
            **evidence,
            "analysis": analysis,
        }
        response = _ask(
            markdown,
            "Review result",
            data,
            ResultReviewResponse,
            state.get("feedback", ""),
            REVIEW_CAPABILITIES,
            role="result_review",
            agents=state.get("agents"),
        )
        problem = _check_evidence(response.evidence, [evidence])
        if problem:
            return _next(
                "review_result", state, "result_review", response, feedback=problem
            )
        experiment = project.vault.load(state["current"])
        _review(
            experiment,
            "result",
            response,
            state["review_rounds"],
            {
                "own_verdict": response.own_verdict,
                "sources": response.sources,
                "evidence": [item.model_dump() for item in response.evidence],
                "agent": response.agent,
            },
        )
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "result_review", response, feedback="")

    def fix(state: ResearchState) -> Command[Literal["fix", "select"]]:
        experiment = project.vault.load(state["current"])
        fixing = dict(experiment.fixing)
        kind = str(fixing["kind"])
        if not fixing.get("snapshot"):
            # Taken once, before the question: a resumed node runs again from the
            # top, and by then the author's changes are in place.
            fixing["snapshot"] = _snapshot(experiment, project)
            experiment.fixing = fixing
            project.vault.save(experiment, overwrite=True)
        snapshot = fixing["snapshot"]
        task = _task(state["task"], project)
        data: dict[str, Any] = {
            "kind": kind,
            "task": _describe(task),
            "metrics": _metrics(task),
            "experiment": _describe(experiment),
            "findings": fixing["findings"],
            "thread": _thread(experiment),
            "command": state["command"],
            "tree": _tree(state["task"], project, state["max_depth"], _reviews(state)),
        }
        if kind in ("result", "acceptance"):
            data.update(_evidence(experiment, project))
        if kind == "code":
            parent = project.vault.load(str(experiment.parent_id))
            base = parent.commit if parent.type == "experiment" else None
            data["diff"] = f"git diff {base or str(experiment.commit) + '^'} " + str(
                experiment.commit
            )
        response = _ask(
            markdown,
            "Fix",
            data,
            FixResponse,
            state.get("feedback", ""),
            FIX_CAPABILITIES,
            role="author",
            agents=state.get("agents"),
        )

        def retry(message: str) -> Command[Literal["fix", "select"]]:
            return _next("fix", state, "author", response, feedback=message)

        wanted = sorted(item["id"] for item in fixing["findings"])
        given = sorted(item.id for item in response.responses)
        if given != wanted:
            return retry("Answer every finding exactly once: " + ", ".join(wanted))
        experiment = project.vault.load(state["current"])
        problem = _apply_fix(project, experiment, kind, response, snapshot)
        if problem:
            project.vault.save(experiment, overwrite=True)
            return retry(problem)
        outside = _scope_changes(project, experiment.id, snapshot["scope"])
        if outside:
            experiment.researcher = {
                "stage": _stage(kind),
                "reason": "scope",
                "findings": [_scope_finding(outside)],
                "records": outside,
            }
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "author", response, feedback="")

    def meta_review(
        state: ResearchState,
    ) -> Command[Literal["meta_review", "select"]]:
        experiment = project.vault.load(state["current"])
        pending = dict(experiment.meta_pending)
        kind = str(pending["kind"])
        task = _task(state["task"], project)
        parent = project.vault.load(str(experiment.parent_id))
        data: dict[str, Any] = {
            "stage": kind,
            # The whole study, so a ruling can weigh the experiment against it.
            "task": _describe(task),
            "metrics": _metrics(task),
            "project_setup": _project_setup(project),
            "tree": _tree(state["task"], project, state["max_depth"], _reviews(state)),
            "parent": _describe(parent),
            "experiment": _describe(experiment),
            "open_findings": pending["findings"],
            "thread": _thread(experiment),
            "command": state["command"],
        }
        evidence = None
        if kind == "result":
            evidence = _evidence(experiment, project)
            data.update(evidence)
        if kind == "code":
            base = parent.commit if parent.type == "experiment" else None
            data["diff"] = f"git diff {base or str(experiment.commit) + '^'} " + str(
                experiment.commit
            )
        response = _ask(
            markdown,
            "Meta review",
            data,
            MetaReviewResponse,
            state.get("feedback", ""),
            [*REVIEW_CAPABILITIES, "shell.execute"],
            role="meta_review",
            agents=state.get("agents"),
        )

        def retry(message: str) -> Command[Literal["meta_review", "select"]]:
            return _next(
                "meta_review", state, "meta_review", response, feedback=message
            )

        wanted = sorted(item["id"] for item in pending["findings"])
        given = sorted(item.finding_id for item in response.rulings)
        if given != wanted:
            return retry("Rule on every open finding once: " + ", ".join(wanted))
        problem = _check_evidence(response.evidence, [evidence] if evidence else [])
        if problem:
            return retry(problem)
        experiment = project.vault.load(state["current"])
        _rule(experiment, kind, pending, response)
        project.vault.save(experiment, overwrite=True)
        return _next("select", state, "meta_review", response, feedback="")

    def researcher(
        state: ResearchState,
    ) -> Command[Literal["researcher", "select"]]:
        experiment = project.vault.load(state["current"])
        task = _task(state["task"], project)
        data = {
            "task": _describe(task),
            "experiment": _describe(experiment),
            "question": experiment.researcher,
            "thread": _thread(experiment),
            "tree": _tree(state["task"], project, state["max_depth"], _reviews(state)),
        }
        response = _ask(
            markdown,
            "Researcher",
            data,
            ResearcherResponse,
            state.get("feedback", ""),
            ["record.read", "workflow.user_input"],
            approval=RESEARCHER,
        )
        experiment = project.vault.load(state["current"])
        _answer(experiment, response)
        project.vault.save(experiment, overwrite=True)
        return Command(update={"feedback": ""}, goto="select")

    def summarize(state: ResearchState) -> Command[Literal["summarize", "select"]]:
        task = _task(state["task"], project)
        tree = _tree(state["task"], project, state["max_depth"], _reviews(state))
        data = {
            "task": _describe(task),
            "metrics": _metrics(task),
            "tree": tree,
            "best_results": _best_results(state["task"], project),
            "disputed": _disputed_events(tree),
            "provisional": _provisional(tree),
            "reopened": int(getattr(task, "acceptance_rounds", 0) or 0),
            "acceptance_feedback": getattr(task, "acceptance_log", None) or [],
        }
        response = _ask(
            markdown,
            "Summarize",
            data,
            SummaryResponse,
            state.get("feedback", ""),
            ["record.read", "tracking.run.read"],
            role="author",
            agents=state.get("agents"),
        )
        task = _task(state["task"], project)
        task.report = response.report
        project.vault.save(task, overwrite=True)
        return _next("select", state, "author", response, feedback="")

    def accept(state: ResearchState) -> Command[Literal["accept", "select"]]:
        task = _task(state["task"], project)
        tree = _tree(state["task"], project, state["max_depth"], _reviews(state))
        experiments = [project.vault.load(item["id"]) for item in tree]
        evidence = {
            item.id: _evidence(item, project) for item in experiments if item.commit
        }
        data = {
            "task": _describe(task),
            "metrics": _metrics(task),
            "report": getattr(task, "report", ""),
            "tree": tree,
            "evidence": evidence,
            "disputed": _disputed_events(tree),
            "provisional": _provisional(tree),
            "command": state["command"],
            "reopen_allowed": int(getattr(task, "acceptance_rounds", 0) or 0)
            < state["acceptance_rounds"],
        }
        response = _ask(
            markdown,
            "Acceptance review",
            data,
            AcceptanceResponse,
            state.get("feedback", ""),
            REVIEW_CAPABILITIES,
            role="accept_review",
            agents=state.get("agents"),
        )

        def retry(message: str) -> Command[Literal["accept", "select"]]:
            return _next("accept", state, "accept_review", response, feedback=message)

        problem = _check_evidence(response.evidence, list(evidence.values()))
        if problem:
            return retry(problem)
        known = {item.id: item for item in experiments}
        if (response.outcome == "reopen") != bool(response.reopen):
            return retry("Send `reopen` entries exactly when the outcome is `reopen`.")
        if any(item.record_id not in known for item in response.reopen):
            return retry("Reopen only experiments of this study.")
        task = _task(state["task"], project)
        count = int(getattr(task, "acceptance_rounds", 0) or 0)
        log = [*(getattr(task, "acceptance_log", None) or [])]
        entry: dict[str, Any] = {
            "round": count + 1,
            "outcome": response.outcome,
            "caveats": response.caveats,
            "sources": response.sources,
            "agent": response.agent,
        }
        if response.outcome == "reopen" and count < state["acceptance_rounds"]:
            task.acceptance_rounds = count + 1
            task.report = None
            task.acceptance = None
            for item in response.reopen:
                experiment = known[item.record_id]
                _reopen(experiment, count + 1, item.findings)
                project.vault.save(experiment, overwrite=True)
            entry["reopened"] = [item.record_id for item in response.reopen]
            task.acceptance_log = [*log, entry]
        else:
            caveats = list(response.caveats)
            outcome = response.outcome
            if outcome == "reopen":
                # The budget is spent: record what stays open.
                outcome = "not_accepted"
                caveats += [
                    f"{item.record_id}: {finding.claim}"
                    for item in response.reopen
                    for finding in item.findings
                ]
            task.acceptance = {"outcome": outcome, "caveats": caveats}
            task.acceptance_log = [*log, entry]
        project.vault.save(task, overwrite=True)
        return _next("select", state, "accept_review", response, feedback="")

    def triage(state: ResearchState) -> Command[Literal["triage", "select"]]:
        experiment = project.vault.load(state["current"])
        tree = _tree(state["task"], project, state["max_depth"], _reviews(state))
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
            _tree(state["task"], project, state["max_depth"], _reviews(state))
            if state.get("task")
            else []
        )
        best = _best_results(state["task"], project) if state.get("task") else []
        task = _task(state["task"], project) if state.get("task") else None
        return {
            "final": markdown.section("## Done").replace(
                "{{task}}", state.get("task", "")
            )
            + "\n\n"
            + _format_summary(tree, best, getattr(task, "acceptance", None))
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
        ("fix", fix),
        ("meta_review", meta_review),
        ("researcher", researcher),
        ("summarize", summarize),
        ("accept", accept),
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
    role: str = "",
    agents: dict[str, str] | None = None,
) -> Any:
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
            parsed = response_model.model_validate(response)
        except PydanticValidationError as exc:
            payload = {**payload, "error": f"Response does not match the schema: {exc}"}
            continue
        clash = _role_clash(agents, role, getattr(parsed, "agent", ""))
        if clash:
            payload = {**payload, "error": clash}
            continue
        return parsed


def _role_clash(agents: dict[str, str] | None, role: str, agent: str) -> str:
    """Reject an agent ID that already acted in another role of this study.

    The ID is self-reported: this catches one conversation doing two jobs by
    mistake, not a deliberate evasion.
    """
    other = (agents or {}).get(agent) if role and agent else None
    if other and other != role:
        return (
            f"Agent ID {agent!r} already acted as {other}; a {role} must be a "
            "different session."
        )
    return ""


def _next(
    goto: str, state: ResearchState, role: str, response: Any, **update: Any
) -> Command[Any]:
    agent = getattr(response, "agent", "")
    agents = {**state.get("agents", {}), **({agent: role} if agent else {})}
    return Command(update={**update, "agents": agents}, goto=goto)


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


def _reviews(state: ResearchState) -> list[str]:
    """The review stages that are on: none unless the study is autonomous."""
    return list(state.get("reviews", [])) if _autonomous(state) else []


def _review_on(state: ResearchState, stage: str) -> bool:
    return stage in _reviews(state)


def _approval(state: ResearchState) -> str:
    return AUTONOMOUS if _autonomous(state) else APPROVAL


def _held(experiment: Record) -> bool:
    """An experiment that waits for a fix, a meta review or the researcher."""
    return any(
        getattr(experiment, field, None)
        for field in ("fixing", "meta_pending", "researcher")
    )


def _batch(state: ResearchState, project: Project) -> list[Record]:
    """The experiments under review: the current one's unreviewed siblings."""
    current = project.vault.load(state["current"])
    siblings = _children(project).get(str(current.parent_id), [])
    return [
        item
        for item in siblings
        if item.commit is None
        and getattr(item, "design_review", None) is None
        and not _held(item)
    ] or [current]


def _thread(experiment: Record) -> list[dict[str, Any]]:
    return list(getattr(experiment, "reviews", None) or [])


def _log_review(experiment: Record, entry: dict[str, Any]) -> None:
    experiment.reviews = [*_thread(experiment), entry]


def _stage(kind: str) -> str:
    """The review stage a fix belongs to; an acceptance reopen is a result fix."""
    return "result" if kind == "acceptance" else kind


def _limit(experiment: Record, finding: dict[str, Any], source: str) -> None:
    experiment.limitations = [
        *(getattr(experiment, "limitations", None) or []),
        {"id": finding["id"], "claim": finding["claim"], "source": source},
    ]


def _with_limitations(experiment: Record) -> None:
    """Write the recorded limitations into the conclusion."""
    limitations = getattr(experiment, "limitations", None) or []
    if not limitations or not getattr(experiment, "conclusion", None):
        return
    body = str(experiment.conclusion).split("\n\nLimitations:")[0]
    lines = "\n".join(f"- {item['claim']}" for item in limitations)
    experiment.conclusion = f"{body}\n\nLimitations:\n{lines}"


def _approve(experiment: Record, kind: str, by: str) -> None:
    stage = _stage(kind)
    if stage == "design":
        experiment.design_review = "approved"
    elif stage == "code":
        experiment.code_review = experiment.commit
    else:
        experiment.result_review = "approved"
        _with_limitations(experiment)
    experiment.fixing = experiment.meta_pending = experiment.researcher = None
    if getattr(experiment, "review_failed", None):
        experiment.review_failed = None
    if by != "reviewer":
        experiment.approved_by = {
            **(getattr(experiment, "approved_by", None) or {}),
            stage: by,
        }


def _conclude(
    experiment: Record, kind: str, why: str, findings: list[dict[str, Any]]
) -> None:
    """End an experiment whose review did not pass, marked in `review_failed`.

    One that ran keeps its analysis as a provisional result, so triage can still
    follow it up; one that never ran has no result, is inconclusive, and stops.
    """
    stage = _stage(kind)
    ran = stage == "result"
    experiment.fixing = experiment.meta_pending = experiment.researcher = None
    experiment.review_failed = {
        "stage": stage,
        "reason": why,
        "findings": [{"id": item["id"], "claim": item["claim"]} for item in findings],
    }
    experiment.state = "analyzed"
    experiment.result_review = "failed" if ran else "not needed"
    if ran and getattr(experiment, "verdict", None):
        _with_limitations(experiment)
        return
    claims = "; ".join(item["claim"] for item in findings)
    head = NOT_RUN.get(stage, "The result did not pass review.")
    experiment.verdict = "inconclusive"
    experiment.conclusion = f"{head} {why}: {claims}"
    if not ran:
        experiment.triage = "stop"
        experiment.triage_reason = "The experiment never ran."


def _review(
    experiment: Record,
    kind: str,
    judgement: Judgement,
    rounds: int,
    extra: dict[str, Any],
) -> None:
    """Record a review and route the experiment on its decision."""
    disputed = _disputed(experiment, kind)
    number = 1 + sum(1 for item in _thread(experiment) if item.get("kind") == kind)
    findings = [
        {"id": f"{kind}-{number}.{index}", **item.model_dump()}
        for index, item in enumerate(judgement.findings, start=1)
    ]
    _log_review(
        experiment,
        {
            "kind": kind,
            "round": number,
            "decision": judgement.decision,
            "findings": findings,
            **extra,
        },
    )
    blocking = [item for item in findings if item["severity"] == "blocking"]
    for item in findings:
        if item["severity"] == "minor":
            _limit(experiment, item, "reviewer")
    if judgement.decision == "approve":
        _approve(experiment, kind, "reviewer")
        return
    unfixable = [item for item in blocking if item["unfixable"]]
    if unfixable:
        _conclude(experiment, kind, "No fix can resolve it", unfixable)
        return
    outside = [item for item in blocking if item["affects"] in ("task", "setup")]
    if outside:
        experiment.researcher = {
            "stage": kind,
            "reason": "finding",
            "findings": blocking,
            "records": [],
        }
        return
    sent_back = dict(getattr(experiment, "rounds", None) or {})
    kept = any(item["continues"] in disputed for item in blocking)
    if kept or sent_back.get(kind, 0) >= rounds:
        # Author and reviewer do not converge.
        experiment.meta_pending = {"kind": kind, "findings": blocking}
        return
    sent_back[kind] = sent_back.get(kind, 0) + 1
    experiment.rounds = sent_back
    experiment.fixing = {"kind": kind, "findings": blocking, "snapshot": None}


def _disputed(experiment: Record, kind: str) -> set[str]:
    """Findings that the author's latest response for this stage disputed."""
    for entry in reversed(_thread(experiment)):
        if entry.get("kind") == "response" and entry.get("stage") == kind:
            return {
                item["id"] for item in entry["responses"] if item["action"] == "dispute"
            }
        if entry.get("kind") == kind:
            return set()
    return set()


def _rule(
    experiment: Record,
    kind: str,
    pending: dict[str, Any],
    response: MetaReviewResponse,
) -> None:
    """Apply a meta review: one ruling per open finding, final for this stage."""
    rulings = {item.finding_id: item for item in response.rulings}
    findings = pending["findings"]
    last = next(
        (
            item
            for item in reversed(_thread(experiment))
            if item.get("kind") == "response" and item.get("stage") == kind
        ),
        {},
    )
    _log_review(
        experiment,
        {
            "kind": "meta",
            "stage": kind,
            "rulings": [item.model_dump() for item in response.rulings],
            "findings": findings,
            "author_responses": last.get("responses", []),
            "sources": response.sources,
            "agent": response.agent,
        },
    )
    experiment.disputed = True
    sustained = [item for item in findings if rulings[item["id"]].ruling == "sustain"]
    for item in findings:
        if rulings[item["id"]].ruling == "limitation":
            _limit(experiment, item, "meta_review")
    if sustained:
        _conclude(experiment, kind, "The meta review sustained", sustained)
    else:
        _approve(experiment, kind, "meta_review")


def _answer(experiment: Record, response: ResearcherResponse) -> None:
    """Apply the researcher's answer to a stopped experiment."""
    question = dict(experiment.researcher)
    kind = str(question["stage"])
    findings = question["findings"]
    _log_review(
        experiment,
        {
            "kind": "researcher",
            "stage": kind,
            "answer": response.answer,
            "note": response.note,
            "findings": findings,
        },
    )
    experiment.researcher = None
    if response.answer == "conclude":
        _conclude(experiment, kind, "The researcher ended it", findings)
    elif response.answer == "accept_risk":
        for item in findings:
            _limit(experiment, item, "researcher")
        if question["reason"] == "finding":
            _approve(experiment, kind, "researcher")
    else:
        # Resolved: the stage's reviewer reviews again, with the thread kept.
        experiment.fixing = None
        setattr(experiment, STAGE_FIELDS[_stage(kind)], None)


def _reopen(experiment: Record, round_: int, findings: list[Finding]) -> None:
    items = [
        {"id": f"acceptance-{round_}.{index}", **item.model_dump()}
        for index, item in enumerate(findings, start=1)
    ]
    _log_review(
        experiment,
        {"kind": "acceptance", "round": round_, "decision": "fix", "findings": items},
    )
    experiment.fixing = {"kind": "acceptance", "findings": items, "snapshot": None}


def _fingerprint(record: Record) -> str:
    text = json.dumps(record.model_dump(mode="json", exclude={"path"}), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _scope(project: Project, experiment_id: str) -> dict[str, str]:
    """What a fix to one experiment must leave alone: the task, the setup, others."""
    return {
        item.id: _fingerprint(item)
        for item in project.vault.list()
        if item.id != experiment_id
        and (item.type in ("task", "experiment") or item.id == "project-setup")
    }


def _scope_changes(
    project: Project, experiment_id: str, before: dict[str, str]
) -> list[str]:
    after = _scope(project, experiment_id)
    return sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )


def _scope_finding(records: list[str]) -> dict[str, Any]:
    return {
        "id": "scope-1",
        "claim": "The fix changed records outside this experiment: "
        + ", ".join(records),
        "evidence": "The records differ from before the fix.",
        "severity": "blocking",
        "affects": "task",
        "suggested_fix": "",
        "unfixable": False,
        "continues": "",
    }


def _plan_of(experiment: Record) -> dict[str, str]:
    return {
        "summary": str(getattr(experiment, "summary", "")),
        "hypothesis": str(getattr(experiment, "hypothesis", "")),
        "expected": str(getattr(experiment, "expected", "")),
        "change": str(experiment.markdown).strip(),
    }


def _snapshot(experiment: Record, project: Project) -> dict[str, Any]:
    return {
        "commit": experiment.commit,
        "plan": _plan_of(experiment),
        "runs": sorted(run.id for run in _matching_runs(experiment, project)),
        "scope": _scope(project, experiment.id),
    }


def _apply_fix(
    project: Project,
    experiment: Record,
    kind: str,
    response: FixResponse,
    snapshot: dict[str, Any],
) -> str:
    """Apply the author's fix and route on what changed; return an error or ''.

    What changed is read from the records and the runs, not from the response.
    """
    stage = _stage(kind)
    changed = False
    if (
        stage != "design"
        and snapshot["runs"]
        and _plan_of(experiment) != snapshot["plan"]
    ):
        plan = snapshot["plan"]
        experiment.hypothesis = plan["hypothesis"]
        experiment.expected = plan["expected"]
        experiment.markdown = plan["change"] + "\n"
        return (
            "The hypothesis, the expected outcome and the change are frozen once "
            "runs exist; they were restored. Open a new experiment for a new idea."
        )
    sha = ""
    if response.commit:
        try:
            sha = GitRepository(project.root).resolve(response.commit)
        except HirakuError as exc:
            return str(exc)
        if not (response.smoke_command.strip() and response.smoke_output.strip()):
            return "Run a smoke test of the change and send its command and output."
    if stage == "design":
        if response.plan:
            plan = response.plan
            if plan.record_id != experiment.id:
                return f"The plan must be for {experiment.id!r}."
            experiment.summary = plan.summary
            experiment.hypothesis = plan.hypothesis
            experiment.expected = plan.expected
            experiment.markdown = f"## Change\n\n{plan.change.strip()}\n"
        changed = _plan_of(experiment) != snapshot["plan"]
    elif stage == "code":
        if sha and sha != snapshot["commit"]:
            _set_commit(experiment, sha, response)
            changed = True
    else:
        known = {run.id for run in _all_runs(experiment, project)}
        unknown = [
            item.run_id for item in response.excluded_runs if item.run_id not in known
        ]
        if unknown:
            return f"Runs not found for this experiment: {', '.join(unknown)}"
        if response.excluded_runs:
            merged = {
                item["run_id"]: item
                for item in getattr(experiment, "excluded_runs", None) or []
            }
            merged.update(
                {item.run_id: item.model_dump() for item in response.excluded_runs}
            )
            experiment.excluded_runs = list(merged.values())
        runs = sorted(run.id for run in _matching_runs(experiment, project))
        reset = _restart if not _children(project).get(experiment.id) else _keep_triage
        if sha and sha != snapshot["commit"]:
            _set_commit(experiment, sha, response)
            experiment.code_review = None
            reset(experiment)
            experiment.verdict = experiment.conclusion = None
            changed = True
        elif runs != snapshot["runs"]:
            experiment.state = "launched"
            reset(experiment)
            experiment.verdict = experiment.conclusion = None
            changed = True
        elif response.verdict and (
            response.verdict != experiment.verdict
            or response.conclusion not in (None, experiment.conclusion)
        ):
            experiment.verdict = response.verdict
            experiment.conclusion = response.conclusion or experiment.conclusion
            changed = True
        experiment.result_review = None
    if any(item.action == "done" for item in response.responses) and not changed:
        return (
            "You reported a fix as done, but nothing observable changed (no new "
            "commit, runs, plan or conclusion). Dispute the finding or record a "
            "limitation instead."
        )
    findings = {item["id"]: item for item in experiment.fixing["findings"]}
    for item in response.responses:
        if item.action == "limitation":
            _limit(experiment, findings[item.id], "author")
    _log_review(
        experiment,
        {
            "kind": "response",
            "stage": stage,
            "responses": [item.model_dump() for item in response.responses],
            "agent": response.agent,
        },
    )
    experiment.fixing = None
    return ""


def _set_commit(experiment: Record, sha: str, response: FixResponse) -> None:
    experiment.commit = sha
    experiment.state = "implemented"
    experiment.smoke_command = response.smoke_command or None
    experiment.smoke_output = response.smoke_output[-SMOKE_OUTPUT_LIMIT:] or None


def _restart(experiment: Record) -> None:
    experiment.triage = None
    experiment.triage_reason = None


def _keep_triage(experiment: Record) -> None:
    """Follow-ups already exist: their parent's triage stays."""


def _step_of(
    experiment: Record,
    depth: int,
    state: ResearchState,
    children: dict[str, list[Record]],
    project: Project,
) -> str:
    # An experiment that waits on a review exit exists only if a review ran.
    if getattr(experiment, "researcher", None):
        return "researcher"
    if getattr(experiment, "meta_pending", None):
        return "meta_review"
    if getattr(experiment, "fixing", None):
        return "fix"
    if getattr(experiment, "verdict", None):
        if (
            _review_on(state, "result")
            and experiment.state == "analyzed"
            and getattr(experiment, "result_review", None) is None
        ):
            return "review_result"
        if getattr(experiment, "triage", None) is None:
            return "triage"
        if (
            experiment.triage == "follow-up"
            and not children.get(experiment.id)
            and depth < state["max_depth"]
        ):
            return "design"
        return "done"
    if not experiment.commit:
        if (
            _review_on(state, "design")
            and getattr(experiment, "design_review", None) is None
        ):
            return "review_design"
        return "implement"
    if (
        _review_on(state, "code")
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


def _project_setup(project: Project) -> str:
    """The `project-setup` context, for the steps that build, launch or check runs.

    Only it says how this project submits jobs and where their output goes, and
    an agent may no longer have it in view after a long or resumed session.
    """
    try:
        return str(project.vault.load("project-setup").markdown)
    except RecordNotFoundError:
        return ""


def _all_runs(experiment: Record, project: Project) -> list[Run]:
    if not Leaderboard.list(project):
        return []
    return list(Leaderboard.load(project).list_runs(project, record_id=experiment.id))


def _matching_runs(experiment: Record, project: Project) -> list[Run]:
    if not experiment.commit:
        return []
    excluded = {
        item["run_id"] for item in getattr(experiment, "excluded_runs", None) or []
    }
    return [
        run
        for run in _all_runs(experiment, project)
        if run.commit == experiment.commit and run.id not in excluded
    ]


def _left_out_runs(experiment: Record, project: Project) -> list[dict[str, Any]]:
    """Runs of the record that have metrics but not the experiment's commit.

    Such a run never reaches the analysis. A run without a commit usually means
    that the command does not record it in the tracking source; a run with
    another commit comes from an earlier commit of the experiment.
    """
    if not experiment.commit:
        return []
    return [
        {"run_id": run.id, "commit": run.commit}
        for run in _all_runs(experiment, project)
        if run.metrics and run.commit != experiment.commit
    ]


def _runs_by_record(
    left_out: dict[str, list[dict[str, Any]]], *, recorded: bool
) -> dict[str, list[str]]:
    """Run IDs per record whose commit is (or is not) recorded."""
    selected = {
        key: [run["run_id"] for run in runs if bool(run["commit"]) is recorded]
        for key, runs in left_out.items()
    }
    return {key: run_ids for key, run_ids in selected.items() if run_ids}


def _list_runs(runs: dict[str, list[str]]) -> str:
    return "; ".join(f"{key}: {', '.join(run_ids)}" for key, run_ids in runs.items())


def _run_data(run: Run, reason: str = "") -> dict[str, Any]:
    data = {
        "run_id": run.id,
        "job_id": run.job_id,
        "metrics": dict(run.metrics),
        "params": dict(run.params),
        "commit": run.commit,
        "timestamp": run.timestamp.isoformat() if run.timestamp else None,
        "duration": run.duration,
        "url": run.url,
        "raw": dict(run.raw),
    }
    return {**data, "reason": reason} if reason else data


def _evidence(experiment: Record, project: Project) -> dict[str, Any]:
    """Every run the tracking source holds for the experiment, raw.

    Runs that the analysis leaves out stay visible with the reason, so that a
    reviewer can check for selection.
    """
    excluded = {
        item["run_id"]: item["reason"]
        for item in getattr(experiment, "excluded_runs", None) or []
    }
    used, left_out = [], []
    for run in _all_runs(experiment, project):
        if run.id in excluded:
            left_out.append(_run_data(run, excluded[run.id]))
        elif run.commit != experiment.commit:
            left_out.append(_run_data(run, "commit differs from the experiment's"))
        else:
            used.append(_run_data(run))
    names = Leaderboard.list(project)
    return {
        "leaderboard": {"name": names[0] if len(names) == 1 else names},
        "runs": used,
        "excluded_runs": left_out,
    }


def _check_evidence(items: list[Evidence], evidences: list[dict[str, Any]]) -> str:
    """Check cited run values against the data; return an error or ''."""
    runs = {
        run["run_id"]: run
        for evidence in evidences
        for run in [*evidence["runs"], *evidence["excluded_runs"]]
    }
    if runs and not items:
        return "Cite the run values your findings rest on in `evidence`."
    for item in items:
        run = runs.get(item.run_id)
        if run is None:
            return f"Run {item.run_id!r} is not in the data."
        value = run["metrics"].get(item.metric)
        if value is None:
            return f"Run {item.run_id!r} has no metric {item.metric!r}."
        if not math.isclose(value, item.value, rel_tol=1e-6, abs_tol=1e-9):
            return f"Run {item.run_id!r} has {item.metric} = {value}, not {item.value}."
    return ""


def _disputed_events(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": item["id"], "rulings": entry["rulings"]}
        for item in tree
        for entry in item["reviews"]
        if entry.get("kind") == "meta"
    ]


def _provisional(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Experiments that ran but whose result did not pass review."""
    return [
        {"id": item["id"], "verdict": item["verdict"], **item["review_failed"]}
        for item in tree
        if item["review_failed"] and item["review_failed"]["stage"] == "result"
    ]


def _tree(
    task_id: str,
    project: Project,
    max_depth: int = 10,
    reviews: Sequence[str] = (),
) -> list[dict[str, Any]]:
    children = _children(project)
    state: ResearchState = {
        "task": task_id,
        "max_depth": max_depth,
        "autonomous": bool(reviews),
        "reviews": list(reviews),
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
                "limitations": getattr(record, "limitations", None) or [],
                "disputed": bool(getattr(record, "disputed", False)),
                "review_failed": getattr(record, "review_failed", None),
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
    fields = record.model_dump(exclude={"path", "markdown", "fixing"})
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
                "provisional": bool(getattr(record, "review_failed", None)),
            }
        )
    return results


def _format_summary(
    tree: list[dict[str, Any]],
    best: list[dict[str, Any]],
    acceptance: dict[str, Any] | None = None,
) -> str:
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
    disputed = [item["id"] for item in tree if item.get("disputed")]
    if disputed:
        lines.extend(["", "## Disputed", "", ", ".join(disputed)])
    if acceptance:
        lines.extend(["", "## Acceptance", "", f"Outcome: {acceptance['outcome']}."])
        lines.extend(f"- {caveat}" for caveat in acceptance.get("caveats", []))
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
