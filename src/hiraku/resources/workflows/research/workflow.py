"""Checkpointed research workflow over Hiraku task and experiment records."""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypedDict

from hiraku.config import HirakuConfig
from hiraku.run import Run
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import HirakuError, ValidationError
from hiraku.execution import validate_command
from hiraku.git import GitRepository
from hiraku.markdown import Markdown
from hiraku.record import Record, list_records, load_record

Verdict = Literal["supports", "refutes", "inconclusive"]
TriageDecision = Literal["follow-up", "stop"]
WORK_PRIORITIES = ("launch", "implement", "triage", "analyze", "design")


class ResearchState(TypedDict, total=False):
    arguments: dict[str, Any]
    run_id: str
    task: str
    command: list[str]
    max_experiments: int
    max_depth: int
    max_repairs: int
    current: str
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


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the graph that guides setup, design, execution, and analysis."""
    path = Path(__file__).with_name("workflow.md")
    fields, body = Record._parse_markdown(path)
    if fields.get("id") != "research":
        raise ValueError("workflow.md must declare id: research")
    markdown = Markdown(body)

    def initialize(state: ResearchState) -> Command[Literal["select"]]:
        try:
            arguments = ResearchInput.model_validate(state.get("arguments", {}))
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc
        task = arguments.task
        if task is None:
            active = config.active_record_id
            if active:
                try:
                    active_record = load_record(active, config=config)
                except HirakuError:
                    active_record = None
                if active_record is not None and active_record.type == "task":
                    task = active
        if task:
            try:
                record = load_record(task, config=config)
            except HirakuError as exc:
                raise ValidationError(str(exc)) from exc
            if record.type != "task":
                raise ValidationError(f"Research root {task!r} must be a task record")
        return Command(
            update={
                "task": task or "",
                "command": validate_command(arguments.command),
                "max_experiments": arguments.max_experiments,
                "max_depth": arguments.max_depth,
                "max_repairs": arguments.max_repairs,
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
            "implement",
            "launch",
            "wait",
            "analyze",
            "triage",
            "done",
        ]
    ]:
        problems = _setup_problems(config)
        if problems:
            return Command(update={"step": "setup"}, goto="setup")
        task_id = state.get("task") or ""
        task_update: dict[str, Any] = {}
        if not task_id:
            active = config.active_record_id
            if active:
                with _ignore_hiraku_errors():
                    record = load_record(active, config=config)
                    if record.type == "task":
                        task_id = active
            if not task_id:
                return Command(update={"task": "", "step": "draft"}, goto="draft")
            task_update["task"] = task_id

        _task(task_id, config)
        tree = _tree(task_id, config, state["max_depth"])
        if len(tree) > state["max_experiments"]:
            # Existing studies are never hidden or truncated by a new session.
            remaining = 0
        else:
            remaining = state["max_experiments"] - len(tree)

        candidates: list[tuple[str, str]] = []
        waiting = False
        children = _children(config)
        for experiment, depth in _ordered(task_id, children):
            step = _step_of(experiment, depth, state, children, config)
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
            return Command(
                update={**task_update, "step": step, "current": current}, goto=step
            )
        if waiting:
            return Command(
                update={**task_update, "step": "wait", "current": ""}, goto="wait"
            )
        return Command(
            update={**task_update, "step": "done", "current": ""}, goto="done"
        )

    def setup(state: ResearchState) -> Command[Literal["setup", "select", "done"]]:
        data = {
            "problems": _setup_problems(config),
            "slurm": shutil.which("sbatch") is not None,
            "default_dispatch": config.default_dispatch,
            "dispatchers": sorted(
                path.name for path in config.dispatch_dir.glob("*") if path.is_file()
            ),
            "job_scripts": _job_scripts(config.root),
            "tracking_backend": config.tracking_backend,
        }
        _ask(
            markdown,
            "Setup",
            data,
            SetupResponse,
            state.get("feedback", ""),
            ["filesystem.read", "filesystem.write", "workflow.user_input"],
        )
        problems = _setup_problems(config)
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
            task = _task(response.record_id, config)
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="draft")
        return Command(update={"task": task.id, "feedback": ""}, goto="select")

    def design(state: ResearchState) -> Command[Literal["design", "select"]]:
        parent = load_record(state["current"], config=config)
        tree = _tree(state["task"], config, state["max_depth"])
        remaining = max(state["max_experiments"] - len(tree), 0)
        depth = _record_depth(parent, state["task"], config)
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
        known = {record.id for record in list_records(config=config)}
        identifiers = [plan.record_id for plan in response.experiments]
        if len(set(identifiers)) != len(identifiers) or known.intersection(identifiers):
            return Command(
                update={"feedback": "Experiment record IDs must be new and distinct."},
                goto="design",
            )
        existing_tree = _tree(state["task"], config, state["max_depth"])
        sequence = max((int(item["sequence"]) for item in existing_tree), default=0)
        try:
            for index, plan in enumerate(response.experiments, start=1):
                Record.create(
                    plan.record_id,
                    parent_id=parent.id,
                    type="experiment",
                    status="designed",
                    summary=plan.summary,
                    hypothesis=plan.hypothesis,
                    expected=plan.expected,
                    sequence=sequence + index,
                    markdown=f"## Change\n\n{plan.change.strip()}\n",
                    activate=False,
                    config=config,
                )
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="design")
        return Command(update={"feedback": ""}, goto="select")

    def implement(state: ResearchState) -> Command[Literal["implement", "select"]]:
        experiment = load_record(state["current"], config=config)
        parent = load_record(str(experiment.parent_id), config=config)
        data = {
            "task": state["task"],
            "experiment": _describe(experiment),
            "parent_commit": parent.commit if parent.type == "experiment" else None,
            "command": state["command"],
            "failure": getattr(experiment, "failure", None),
            "review_feedback": getattr(experiment, "review_feedback", None),
        }
        response = _ask(
            markdown,
            "Implement",
            data,
            CommitResponse,
            state.get("feedback", ""),
            ["filesystem.read", "filesystem.write", "shell.execute", "git.commit"],
        )
        try:
            sha = GitRepository(config.root).resolve(response.commit)
        except HirakuError as exc:
            return Command(update={"feedback": str(exc)}, goto="implement")
        experiment.commit = sha
        experiment.status = "implemented"
        experiment.failure = None
        experiment.save()
        return Command(update={"feedback": ""}, goto="select")

    def launch(state: ResearchState) -> Command[Literal["launch", "select"]]:
        experiment = load_record(state["current"], config=config)
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
            _failed(experiment.id, response.reason, state, config)
            return Command(update={"feedback": ""}, goto="select")
        experiment.status = "launched"
        experiment.save()
        return Command(update={"feedback": ""}, goto="select")

    def wait(state: ResearchState) -> Command[Literal["wait", "select"]]:
        waiting = [
            item
            for item in _tree(state["task"], config, state["max_depth"])
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
            _failed(response.record_id, response.reason, state, config)
            return Command(update={"feedback": ""}, goto="select")
        fresh = _tree(state["task"], config, state["max_depth"])
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
        experiment = load_record(state["current"], config=config)
        parent = load_record(str(experiment.parent_id), config=config)
        task = _task(state["task"], config)
        runs = _matching_runs(experiment, config)
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
        )
        if not usable:
            return Command(
                update={"feedback": "No attributed run with metrics is available yet."},
                goto="analyze",
            )
        experiment.status = "analyzed"
        experiment.verdict = response.verdict
        experiment.conclusion = response.conclusion
        experiment.save()
        return Command(update={"feedback": ""}, goto="select")

    def triage(state: ResearchState) -> Command[Literal["triage", "select"]]:
        experiment = load_record(state["current"], config=config)
        tree = _tree(state["task"], config, state["max_depth"])
        depth = _record_depth(experiment, state["task"], config)
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
        )
        if response.decision == "follow-up" and not can_follow_up:
            reason = (
                f"Cannot follow up: maximum depth {state['max_depth']} or "
                f"experiment limit {state['max_experiments']} reached."
            )
            return Command(update={"feedback": reason}, goto="triage")
        experiment.triage = response.decision
        experiment.triage_reason = response.reason
        experiment.save()
        return Command(update={"feedback": ""}, goto="select")

    def done(state: ResearchState) -> dict[str, str]:
        tree = (
            _tree(state["task"], config, state["max_depth"])
            if state.get("task")
            else []
        )
        best = _best_results(state["task"], config) if state.get("task") else []
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
) -> BaseModel:
    payload: dict[str, Any] = {
        "step": title.lower().replace(" ", "_"),
        "instruction": markdown.section(f"## {title}"),
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


def _setup_problems(config: HirakuConfig) -> list[str]:
    problems = []
    contexts = list_records(config=config)
    if not any(
        item.id == "project-setup" and item.type == "context" for item in contexts
    ):
        problems.append(
            "no `project-setup` context says how experiments run in this project"
        )
    dispatch = config.default_dispatch
    path = config.dispatch_dir / str(dispatch)
    if dispatch and not path.is_file():
        problems.append(f"the default dispatcher {dispatch} is not in hiraku/dispatch")
    elif dispatch and not os.access(path, os.X_OK):
        problems.append(f"the default dispatcher {dispatch} is not executable")
    if shutil.which("sbatch") and dispatch in ("launch.sh", "worktree.sh"):
        problems.append(
            f"this is a Slurm cluster, but {dispatch} would run experiments "
            "on this node; "
            "set default_dispatch: slurm.sh in hiraku.yml"
        )
    if dispatch == "slurm.sh":
        header = config.dispatch_dir / "slurm.header"
        text = header.read_text(encoding="utf-8") if header.is_file() else ""
        if not any(line.startswith("#SBATCH") for line in text.splitlines()):
            problems.append(
                "hiraku/dispatch/slurm.header has no #SBATCH lines for this site"
            )
    return problems


def _task(record_id: str, config: HirakuConfig) -> Record:
    record = load_record(record_id, config=config)
    if record.type != "task":
        raise ValidationError(f"Record {record_id!r} must have type: task")
    if not record.metrics:
        raise ValidationError(f"Task {record_id!r} must declare at least one metric")
    return record


def _metrics(task: Record) -> list[dict[str, Any]]:
    return [item.model_dump() for item in task.metrics or []]


def _children(config: HirakuConfig) -> dict[str, list[Record]]:
    result: dict[str, list[Record]] = {}
    for record in list_records(config=config):
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


def _step_of(
    experiment: Record,
    depth: int,
    state: ResearchState,
    children: dict[str, list[Record]],
    config: HirakuConfig,
) -> str:
    if getattr(experiment, "verdict", None):
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
        return "implement"
    runs = _matching_runs(experiment, config)
    if any(run.metrics for run in runs):
        return "analyze"
    if runs or experiment.status == "launched":
        return "waiting"
    return "launch"


def _matching_runs(experiment: Record, config: HirakuConfig) -> list[Run]:
    if not experiment.commit:
        return []
    return [
        run for run in experiment.runs(config=config) if run.commit == experiment.commit
    ]


def _tree(
    task_id: str,
    config: HirakuConfig,
    max_depth: int = 10,
) -> list[dict[str, Any]]:
    children = _children(config)
    state: ResearchState = {
        "task": task_id,
        "max_depth": max_depth,
    }
    rows = []
    for record, depth in _ordered(task_id, children):
        rows.append(
            {
                "id": record.id,
                "parent": record.parent_id,
                "depth": depth,
                "sequence": int(getattr(record, "sequence", 0) or 0),
                "step": _step_of(record, depth, state, children, config),
                "summary": getattr(record, "summary", None),
                "commit": record.commit,
                "verdict": getattr(record, "verdict", None),
                "triage": getattr(record, "triage", None),
                "conclusion": getattr(record, "conclusion", None),
            }
        )
    return rows


def _tree_order(record_id: str, task_id: str, children: dict[str, list[Record]]) -> int:
    for index, (record, _depth) in enumerate(_ordered(task_id, children)):
        if record.id == record_id:
            return index
    return -1


def _record_depth(record: Record, task_id: str, config: HirakuConfig) -> int:
    depth = 0
    while record.id != task_id:
        if not record.parent_id:
            raise ValidationError(f"{record.id!r} is not under task {task_id!r}")
        record = load_record(record.parent_id, config=config)
        depth += 1
    return depth


def _describe(record: Record) -> dict[str, Any]:
    fields = record.model_dump(exclude={"path", "markdown"})
    fields["markdown"] = str(record.markdown)
    fields["record_id"] = record.id
    return fields


def _failed(
    record_id: str, reason: str, state: ResearchState, config: HirakuConfig
) -> None:
    if not record_id:
        raise ValidationError("A failed event requires the experiment record ID")
    experiment = load_record(record_id, config=config)
    repairs = int(getattr(experiment, "repairs", 0) or 0) + 1
    experiment.repairs = repairs
    experiment.failure = reason
    if repairs > state["max_repairs"]:
        experiment.status = "analyzed"
        experiment.verdict = "inconclusive"
        experiment.conclusion = (
            f"Could not be run after {state['max_repairs']} repairs: {reason}"
        )
        experiment.triage = "stop"
    else:
        experiment.commit = None
        experiment.status = "designed"
    experiment.save()


def _best_results(task_id: str, config: HirakuConfig) -> list[dict[str, Any]]:
    task = _task(task_id, config)
    results = []
    for metric in _metrics(task):
        candidates = []
        for item in _tree(task_id, config):
            record = load_record(item["id"], config=config)
            for run in _matching_runs(record, config):
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
