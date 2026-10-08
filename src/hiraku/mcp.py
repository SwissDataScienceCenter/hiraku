"""MCP tools for Hiraku agentic research operations."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import PromptMessage, ResourceLink, TextContent
from pydantic import BaseModel, Field

from .agent import list_contexts, list_programs, load_context, load_program
from .config import HirakuConfig
from .errors import ValidationError
from .git import GitRepository
from .launch import launch
from .record import Metric, Record, list_records, load_record


class ProposedEntrypoint(BaseModel):
    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ExperimentProposal(BaseModel):
    id: str = Field(min_length=1)
    parent_id: str
    summary: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    implementation_plan: str = Field(min_length=1)
    evaluation_plan: str = Field(min_length=1)
    entrypoint: ProposedEntrypoint | None = None


class ResearchTask(BaseModel):
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    metrics: list[Metric] = Field(min_length=1)
    protocol: str = Field(min_length=1)
    experiment_budget: int = Field(gt=0)


class Analysis(BaseModel):
    run_ids: list[str] = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    decision: str = Field(min_length=1)


_active_config: ContextVar[HirakuConfig | None] = ContextVar(
    "hiraku_mcp_config", default=None
)

mcp = MCPServer(
    name="hiraku",
    title="Hiraku MCP Server",
    description="Hiraku research state and operations",
    instructions="Hiraku research state and operations",
)
_program_prompt_names: set[str] = set()


def _config() -> HirakuConfig:
    """Return the project config bound to this MCP server session."""
    return _active_config.get() or HirakuConfig.discover()


@mcp.tool()
def create_task(task: ResearchTask) -> dict[str, Any]:
    """Create a root research task with its goal, metrics, protocol, and budget."""
    config = _config()
    if any(record.id == task.id for record in list_records(config=config)):
        raise ValidationError(f"Record {task.id!r} already exists")
    record = Record.create(
        task.id,
        type="task",
        goal=task.goal,
        metrics=[metric.model_dump() for metric in task.metrics],
        experiment_budget=task.experiment_budget,
        markdown=f"## Evaluation protocol\n\n{task.protocol}",
        activate=False,
        config=config,
    )
    return {"id": record.id, "type": record.type, "path": str(record.path)}


@mcp.tool()
def list_runs(record_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """List normalized runs visible from a record."""
    if limit < 0:
        raise ValidationError("limit must be non-negative")
    config = _config()
    runs = load_record(record_id, config=config).runs(config=config, limit=limit)
    return [
        {
            "id": run.id,
            "record_id": run.record_id,
            "commit": run.commit,
            "entrypoint": run.entrypoint,
            "metrics": dict(run.metrics),
            "params": dict(run.params),
            "timestamp": run.timestamp,
            "url": run.url,
        }
        for run in runs
    ]


@mcp.tool()
def propose_experiment(proposal: ExperimentProposal) -> dict[str, Any]:
    """Create a proposed child experiment record."""
    config = _config()
    parent = load_record(proposal.parent_id, config=config)
    if any(item.id == proposal.id for item in list_records(config=config)):
        raise ValidationError(f"Record {proposal.id!r} already exists")
    values: dict[str, Any] = {
        "type": "experiment",
        "status": "proposed",
        "hypothesis": proposal.hypothesis,
        "proposal_summary": proposal.summary,
        "approval_status": "pending",
    }
    if proposal.entrypoint:
        values["entrypoints"] = {
            proposal.entrypoint.name: {
                "command": proposal.entrypoint.command,
                "args": proposal.entrypoint.args,
            }
        }
    record = Record.create(
        proposal.id,
        parent_id=parent.id,
        markdown=(
            f"{proposal.summary}\n\n"
            f"## Hypothesis\n\n{proposal.hypothesis}\n\n"
            f"## Implementation plan\n\n{proposal.implementation_plan}\n\n"
            f"## Evaluation plan\n\n{proposal.evaluation_plan}"
        ),
        activate=False,
        config=config,
        **values,
    )
    return {
        "id": record.id,
        "status": record.status,
        "approval_status": record.approval_status,
    }


@mcp.tool()
def approve_experiment(record_id: str, approved_by: str) -> dict[str, Any]:
    """Record explicit human approval for a proposed experiment."""
    if not approved_by.strip():
        raise ValidationError("approved_by must identify the approving user")
    config = _config()
    record = load_record(record_id, config=config)
    if record.type != "experiment" or record.approval_status != "pending":
        raise ValidationError("Only a pending experiment proposal can be approved")
    record.approval_status = "approved"
    record.approved_by = approved_by
    record.approved_at_head = GitRepository(config).head
    record.status = "approved"
    record.save()
    return {
        "id": record.id,
        "approval_status": record.approval_status,
        "approved_by": record.approved_by,
        "approved_at_head": record.approved_at_head,
    }


@mcp.tool()
def commit_implementation(
    record_id: str, paths: list[str], message: str
) -> dict[str, Any]:
    """Validate and commit explicitly selected implementation files."""
    config = _config()
    record = load_record(record_id, config=config)
    if record.type != "experiment" or record.approval_status != "approved":
        raise ValidationError("Implementation requires an approved experiment")
    if not paths or not message.strip():
        raise ValidationError("paths and message must be non-empty")
    git = GitRepository(config)
    if git.head != record.approved_at_head:
        raise ValidationError(
            "HEAD changed since approval; propose and approve against current state"
        )
    resolved_paths: list[str] = []
    for path in paths:
        candidate = (config.root / path).resolve()
        try:
            candidate.relative_to(config.root.resolve())
        except ValueError as exc:
            raise ValidationError(f"Path is outside the project: {path}") from exc
        if candidate == config.records_dir or config.records_dir in candidate.parents:
            raise ValidationError(
                "Research records cannot be included as implementation"
            )
        if not candidate.is_file():
            raise ValidationError(f"Implementation file does not exist: {path}")
        resolved_paths.append(str(candidate.relative_to(config.root.resolve())))
    staged_paths = {
        path
        for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        if path
    }
    unrelated_staged = staged_paths.difference(resolved_paths)
    if unrelated_staged:
        raise ValidationError(
            f"Refusing to commit unrelated staged paths: {sorted(unrelated_staged)}"
        )
    git._git("add", "--", *resolved_paths)
    git._git("diff", "--cached", "--check")
    git._git("-c", "commit.gpgSign=false", "commit", "-m", message)
    commit = git.head
    record.commit = commit
    record.status = "implemented"
    record.save()
    return {"record_id": record.id, "commit": commit, "committed_paths": resolved_paths}


@mcp.tool()
def launch_experiment(
    record_id: str, entrypoint: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Launch an approved, committed experiment through its dispatcher."""
    config = _config()
    record = load_record(record_id, config=config)
    if (
        record.type != "experiment"
        or record.approval_status != "approved"
        or not record.commit
    ):
        raise ValidationError("Launch requires an approved experiment with a commit")
    git = GitRepository(config)
    commit = git.resolve(record.commit)
    if commit != record.commit:
        raise ValidationError("Experiment commit is invalid")
    definition = record.entrypoint(entrypoint, config=config)
    result = launch(
        definition,
        name=entrypoint,
        args=args or {},
        record_id=record.id,
        commit=commit,
        capture_output=True,
        check=False,
        config=config,
    )
    if result.returncode:
        raise ValidationError(
            f"Experiment launch exited with status {result.returncode}"
        )
    return {
        "record_id": record.id,
        "commit": commit,
        "entrypoint": entrypoint,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@mcp.tool()
def record_analysis(record_id: str, analysis: Analysis) -> dict[str, Any]:
    """Validate cited runs and persist the experiment conclusion."""
    config = _config()
    record = load_record(record_id, config=config)
    if record.type != "experiment" or not record.commit:
        raise ValidationError("Analysis requires a committed experiment")
    known = {run.id: run for run in record.runs(config=config)}
    cited = []
    for run_id in analysis.run_ids:
        run = known.get(run_id)
        if run is None or run.record_id != record.id or run.commit != record.commit:
            raise ValidationError(
                f"Run {run_id!r} is not attributed to this experiment commit"
            )
        cited.append(run_id)
    record.conclusion = analysis.conclusion
    record.decision = analysis.decision
    record.analysis_run_ids = cited
    record.status = "analyzed"
    record.save()
    return {
        "record_id": record.id,
        "status": record.status,
        "run_ids": cited,
        "conclusion": record.conclusion,
        "decision": record.decision,
    }


@mcp.resource("hiraku://project", name="project")
def project_resource() -> dict[str, Any]:
    """Expose project configuration and available Hiraku resource IDs."""
    config = _config()
    return {
        "config": config.to_dict(),
        "records": [record.id for record in list_records(config=config)],
        "programs": [program.id for program in list_programs(config=config)],
        "contexts": [context.id for context in list_contexts(config=config)],
    }


@mcp.resource(
    "hiraku://records/{record_id}", name="records", mime_type="application/json"
)
def record_resource(record_id: str) -> str:
    """Expose a record with its inherited effective research context."""
    config = _config()
    record = load_record(record_id, config=config)
    resolved = record.resolve(config=config)
    return json.dumps(
        {
            "record": record.to_dict(),
            "effective": resolved.values,
            "sources": resolved.sources,
            "markdown": record.markdown,
        },
        default=str,
    )


@mcp.resource("hiraku://programs/{program_id}", name="programs")
def program_resource(program_id: str) -> str:
    """Expose a project's research program as Markdown."""
    program = load_program(program_id, config=_config())
    return program.path.read_text(encoding="utf-8")


@mcp.resource("hiraku://contexts/{context_id}", name="contexts")
def context_resource(context_id: str) -> str:
    """Expose a project's curated context as Markdown."""
    context = load_context(context_id, config=_config())
    return context.path.read_text(encoding="utf-8")


def _program_prompt_messages(
    program_id: str,
    task: str | None,
    context_ids: str = "",
    topic: str = "",
    idea: str = "",
) -> list[PromptMessage]:
    config = _config()
    if task is None:
        task = config.active_record_id
    if task is None:
        task_record = None
    else:
        task_record = load_record(task, config=config)
        if task_record.type != "task":
            raise ValidationError(f"Record {task!r} is not a task")

    instruction = (
        f"Follow the Hiraku Program `{program_id}` for the selected task. "
        "Use the linked project resources as authoritative research context. "
        "The coding harness owns reasoning, implementation, iteration, and "
        "user interaction; use Hiraku tools for validated state changes."
    )
    if task_record is None:
        instruction += " Ask the user which task to use before proceeding."
    if idea:
        instruction += f"\n\nUser's research idea: {idea}"
    if topic:
        instruction += f"\n\nResearch topic: {topic}"

    messages = [
        PromptMessage(role="user", content=TextContent(type="text", text=instruction))
    ]
    messages.append(
        PromptMessage(
            role="user",
            content=ResourceLink(
                name=f"Program: {program_id}",
                uri=f"hiraku://programs/{program_id}",
                description="Canonical Hiraku Program instructions",
                mime_type="text/markdown",
            ),
        )
    )
    if task_record is not None:
        messages.append(
            PromptMessage(
                role="user",
                content=ResourceLink(
                    name=f"Task: {task_record.id}",
                    uri=f"hiraku://records/{task_record.id}",
                    description="Task record with resolved inherited context",
                    mime_type="application/json",
                ),
            )
        )
    for context_id in (item.strip() for item in context_ids.split(",")):
        if not context_id:
            continue
        context = load_context(context_id, config=config)
        messages.append(
            PromptMessage(
                role="user",
                content=ResourceLink(
                    name=f"Context: {context.id}",
                    uri=f"hiraku://contexts/{context.id}",
                    description="Curated research context",
                    mime_type="text/markdown",
                ),
            )
        )
    return messages


@mcp.prompt(
    name="propose-experiment",
    title="Propose an experiment",
    description=(
        "Draft an experiment proposal from a task and idea, create its record, "
        "and stop for review."
    ),
)
def propose_experiment_prompt(
    task: str | None = None, idea: str = "", contexts: str = ""
) -> list[PromptMessage]:
    """Create a proposed experiment record, without implementing or launching it."""
    messages = _program_prompt_messages(
        "interactive", task, context_ids=contexts, idea=idea
    )
    first = messages[0].content
    first.text = (
        "Draft a falsifiable experiment proposal from this idea: "
        f"{idea or '(ask the user)'}\n\n"
        f"{first.text}\nDevelop a hypothesis, implementation plan, and "
        "evaluation plan tied to the task's metrics and protocol. Create the "
        "proposed experiment with the `propose_experiment` tool, then stop. "
        "Do not edit source files, commit, or launch a run."
    )
    return messages


def _register_program_prompts(config: HirakuConfig) -> None:
    """Expose project Programs as user-invoked MCP prompts."""
    global _program_prompt_names
    for name in _program_prompt_names:
        mcp.remove_prompt(name)
    _program_prompt_names = set()
    for program in list_programs(config=config):
        if program.id == "propose-experiment":
            raise ValidationError(
                "Program ID 'propose-experiment' is reserved for the built-in prompt"
            )

        def make_prompt(program_id: str):
            def run_program(
                task: str | None = None,
                topic: str = "",
                contexts: str = "",
            ) -> list[PromptMessage]:
                return _program_prompt_messages(
                    program_id, task, context_ids=contexts, topic=topic
                )

            return run_program

        prompt = Prompt.from_function(
            make_prompt(program.id),
            name=program.id,
            title=program.id.replace("-", " ").title(),
            description=program.summary or f"Run the {program.id} Hiraku Program.",
        )
        mcp.add_prompt(prompt)
        _program_prompt_names.add(program.id)


def create_server(config: HirakuConfig | None = None) -> MCPServer:
    """Return Hiraku's module-level MCP API server."""
    selected = config or HirakuConfig.discover()
    _active_config.set(selected)
    _register_program_prompts(selected)
    return mcp


def serve(config: HirakuConfig | None = None) -> None:
    """Run Hiraku's local stdio MCP server."""
    token = _active_config.set(config)
    try:
        _register_program_prompts(config or HirakuConfig.discover())
        mcp.run(transport="stdio")
    finally:
        _active_config.reset(token)
