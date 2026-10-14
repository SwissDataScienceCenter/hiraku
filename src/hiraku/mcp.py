"""Stdio MCP server exposing Hiraku records, runs, and workflows.

The server is the MCP SDK's `MCPServer`. Its tools and prompts are fixed when
it starts, because coding agents do not reliably pick up changes to the tool
list. Each workflow is started by a tool and a prompt named after it, and
every reply goes through one ``workflow_reply`` tool.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import sys
from collections.abc import Callable, Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Protocol

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .config import HirakuConfig
from .context import load_context
from .errors import GitError, HirakuError, NoActiveRecordError, ValidationError
from .git import GitRepository
from .record import Record, list_records, load_record, update_record
from .session import Session
from .workflow import (
    Workflow,
    list_workflows,
    load_workflows,
    summary,
    workflow_description,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, and runs workflows that guide you step by step.
Start a workflow with the tool named after it (workflow_list lists them), and
answer its instructions with workflow_reply. Use session_list and session_show
to resume earlier sessions. Launch experiments with the `hiraku launch` CLI.\
"""

GUIDE = """\
How to follow a Hiraku workflow session:
1. Carry out the instruction. Its context holds the data it refers to.
2. If requires_user_response is true, ask the user and wait for their answer
   before replying; otherwise decide yourself.
3. Continue with the workflow_reply tool: pass one of the listed replies as
   reply, its arguments as described by the reply's schema, and the
   instruction_id and session_id of the response unchanged.
4. Each accepted reply returns the next instruction. Stop when a response has
   completed: true.
5. A response with status rejected or not_ready keeps the same instruction
   pending: fix the input, or check again later for not_ready. Status stale
   means the reply answered an instruction that is no longer pending; continue
   from the current instruction in the response.
6. Repeating an identical reply is safe; it returns the same response.
7. To resume, call session_show with the session_id; it returns the pending
   instruction. Do not start a new session to resume an existing one.
Before repeating consequential work such as creating records or launching
experiments, check whether it already exists.\
"""

MARKDOWN_LIMIT = 8000
DEFAULT_RUN_LIMIT = 20

Project = Callable[[], HirakuConfig]
SessionId = Annotated[str, Field(description="Session ID from session_list.")]


class AddTool(Protocol):
    def __call__(
        self,
        function: Callable[..., Any],
        description: str,
        *,
        read_only: bool,
        name: str | None = None,
    ) -> None: ...


def create_server(config: HirakuConfig) -> MCPServer:
    """Create the Hiraku MCP server for one project.

    Workflows are loaded once; a workflow added or changed later needs a new
    server, e.g. by reconnecting it in the coding agent.
    """
    path = config.path

    def project() -> HirakuConfig:
        # Re-read on every call, so changes made elsewhere, such as a new active
        # record, are visible to a long-running server.
        return HirakuConfig.load(path)

    workflows = load_workflows(config)
    server = MCPServer("hiraku", version=_version(), instructions=INSTRUCTIONS)

    def add_tool(
        function: Callable[..., Any],
        description: str,
        *,
        read_only: bool,
        name: str | None = None,
    ) -> None:
        server.add_tool(
            _json_result(function),
            name=name,
            description=description,
            annotations=ToolAnnotations(read_only_hint=read_only),
            structured_output=False,
        )

    _add_project_tools(add_tool, project)
    _add_workflow_tools(add_tool, project, workflows)
    _add_session_tools(add_tool, project, workflows)
    _add_record_tools(add_tool, project)
    for workflow_id, workflow in sorted(workflows.items()):
        server.add_prompt(
            Prompt.from_function(
                _start_prompt(workflow, project),
                name=workflow_id,
                description=summary(workflow_description(workflow))
                or f"Start the {workflow_id} workflow.",
            )
        )
    _register_resources(server, project, workflows)
    return server


def serve(config: HirakuConfig) -> None:
    """Run the Hiraku MCP server over stdio for one project."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="hiraku: %(message)s"
    )
    anyio.run(create_server(config).run_stdio_async)


def _add_project_tools(add_tool: AddTool, project: Project) -> None:
    def project_status() -> dict[str, Any]:
        config = project()
        try:
            branch = GitRepository(config).current_branch
        except GitError:
            branch = None
        return to_jsonable_python(
            {
                "project_root": config.root,
                "active_record_id": config.active_record_id,
                "tracking_backend": config.tracking_backend,
                "git_branch": branch,
            }
        )

    add_tool(
        project_status,
        "Show the project root, active record, tracking backend, and Git branch.",
        read_only=True,
    )


# Workflows and sessions -------------------------------------------------------


def _add_workflow_tools(
    add_tool: AddTool, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    def workflow_list() -> list[dict[str, Any]]:
        return list_workflows(workflows)

    def workflow_reply(
        reply: Annotated[
            str, Field(description="One of the replies the instruction lists.")
        ],
        instruction_id: Annotated[
            int, Field(description="instruction_id of the pending instruction.")
        ],
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="The reply's arguments, as its schema describes."),
        ] = None,
        session_id: Annotated[
            str | None,
            Field(
                description="session_id of the instruction; may be omitted when "
                "only one session is active."
            ),
        ] = None,
    ) -> dict[str, Any]:
        config = project()
        if session_id is None:
            active = [
                item["session_id"]
                for item in Session.list_all(config=config)
                if item.get("status") == "active"
            ]
            if len(active) != 1:
                return {
                    "status": "rejected",
                    "message": (
                        f"Several sessions are active ({', '.join(active)}); pass "
                        "session_id"
                        if active
                        else "No session is active; start a workflow first"
                    ),
                }
            session_id = active[0]
        session = Session.load(session_id, config=config)
        workflow = workflows.get(session.workflow)
        if workflow is None:
            return session.outcome(
                "rejected",
                f"Workflow {session.workflow!r} is unavailable; the session cannot "
                "continue until it loads.",
            )
        return workflow.bind(config).run_reply(
            session_id, instruction_id, reply, arguments
        )

    add_tool(
        workflow_list,
        "List the project's workflows. Start one with the tool named after it.",
        read_only=True,
    )
    add_tool(
        workflow_reply,
        "Reply to the pending instruction of a workflow session with one of the "
        "replies it lists.",
        read_only=False,
    )
    for workflow_id, workflow in sorted(workflows.items()):
        add_tool(
            _start_tool(workflow, project),
            _start_description(workflow),
            read_only=False,
            name=workflow_id,
        )


def _add_session_tools(
    add_tool: AddTool, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    def session_list() -> list[dict[str, Any]]:
        return Session.list_all(config=project())

    def session_show(session_id: SessionId) -> dict[str, Any]:
        return _show(Session.load(session_id, config=project()), workflows)

    def session_cancel(session_id: SessionId) -> dict[str, Any]:
        return Session.load(session_id, config=project()).cancel()

    add_tool(
        session_list,
        "List workflow sessions with their status, newest first.",
        read_only=True,
    )
    add_tool(
        session_show,
        "Show a workflow session: orientation, pending instruction or result, and "
        "stored data. Use it to resume a session.",
        read_only=True,
    )
    add_tool(
        session_cancel,
        "Cancel a workflow session. Records, code, and runs are kept.",
        read_only=False,
    )


def _show(session: Session, workflows: Mapping[str, Workflow]) -> dict[str, Any]:
    return {**session.show(workflows.get(session.workflow)), "guide": GUIDE}


def _start_tool(workflow: Workflow, project: Project) -> Callable[..., Any]:
    """Return a function whose signature is the workflow's start arguments."""

    def run(**arguments: Any) -> dict[str, Any]:
        response = workflow.bind(project()).run_start(arguments)
        if response["status"] != "accepted":
            return response
        return {"status": "accepted", "guide": GUIDE, **response}

    parameters = []
    for name, field in workflow.start_arguments().model_fields.items():
        default = (
            inspect.Parameter.empty
            if field.is_required()
            else field.get_default(call_default_factory=True)
        )
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=Annotated[
                    (
                        field.annotation,
                        *field.metadata,
                        Field(description=field.description),
                    )
                ],
            )
        )
    return _with_signature(run, workflow.id, parameters)


def _start_prompt(workflow: Workflow, project: Project) -> Callable[..., str]:
    """Return a function that starts a session and renders its instruction.

    Prompt arguments are strings; the start arguments' model converts them.
    """

    def run(**arguments: Any) -> str:
        values = {key: value for key, value in arguments.items() if value is not None}
        response = workflow.bind(project()).run_start(values)
        if response["status"] != "accepted":
            # The SDK hides exception messages from prompts, so explain in text.
            return (
                f"The Hiraku workflow `{workflow.id}` did not start: "
                f"{response.get('message')}. Tell the user and ask for corrected "
                "arguments."
            )
        return _prompt_text(response)

    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if field.is_required() else None,
            annotation=Annotated[
                str if field.is_required() else str | None,  # ty: ignore[invalid-type-form]
                Field(description=field.description),
            ],
        )
        for name, field in workflow.start_arguments().model_fields.items()
    ]
    return _with_signature(run, workflow.id, parameters)


def _with_signature(
    function: Callable[..., Any], workflow_id: str, parameters: list[inspect.Parameter]
) -> Callable[..., Any]:
    function.__name__ = workflow_id.replace("-", "_")  # ty: ignore[unresolved-attribute]
    function.__signature__ = inspect.Signature(parameters)  # ty: ignore[unresolved-attribute]
    # Pydantic's validate_call, used for prompts, reads the annotations.
    function.__annotations__ = {item.name: item.annotation for item in parameters}
    return function


def _start_description(workflow: Workflow) -> str:
    prefix = f"Start a {workflow.id} workflow session."
    if text := summary(workflow_description(workflow)):
        prefix += f" {text}"
    start = inspect.getdoc(getattr(type(workflow), "start", None)) or ""
    return f"{prefix}\n\n{start}" if start else prefix


def _prompt_text(response: Mapping[str, Any]) -> str:
    """Render a start response as a user message for the agent."""
    instruction = response["instruction"]
    parts = []
    orientation = response.get("orientation") or ""
    if orientation and not instruction["text"].startswith(orientation):
        parts.append(orientation)
    parts.append(instruction["text"])
    if instruction.get("context") is not None:
        context = json.dumps(instruction["context"], indent=1, ensure_ascii=False)
        parts.append(f"Context:\n```json\n{context}\n```")
    replies = json.dumps(instruction["replies"], indent=1, ensure_ascii=False)
    parts.append(
        f"---\nThis is Hiraku workflow session `{response['session_id']}`, "
        f"instruction {response['instruction_id']}. Reply with the workflow_reply "
        f"tool, passing session_id `{response['session_id']}`, instruction_id "
        f"{response['instruction_id']}, one of these replies, and its "
        f"arguments:\n```json\n{replies}\n```\n\n{GUIDE}"
    )
    return "\n\n".join(parts)


# Records and runs -------------------------------------------------------------


def _add_record_tools(add_tool: AddTool, project: Project) -> None:
    def record_list() -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "parent_id": record.parent_id,
                "type": record.type,
                "status": record.status,
                "decision": record.decision,
            }
            for record in list_records(config=project())
        ]

    def record_show(
        record_id: Annotated[
            str | None,
            Field(description="Record to show; defaults to the active record."),
        ] = None,
        raw: Annotated[
            bool, Field(description="Show stored fields only, without inheritance.")
        ] = False,
    ) -> dict[str, Any]:
        config = project()
        record = _active_record(record_id, config)
        if raw:
            values, sources = record.to_dict(), {}
        else:
            resolved = record.resolve(config=config)
            values, sources = resolved.values, resolved.sources
        result: dict[str, Any] = {
            "id": record.id,
            "values": to_jsonable_python(values, fallback=dict),
            "sources": to_jsonable_python(sources, fallback=dict),
            "markdown": record.markdown[:MARKDOWN_LIMIT],
        }
        if len(record.markdown) > MARKDOWN_LIMIT:
            result["markdown_truncated"] = True
            result["markdown_resource"] = f"hiraku://records/{record.id}"
        return result

    def record_create(
        record_id: Annotated[str, Field(description="New record ID.")],
        parent_id: Annotated[str | None, Field(description="Parent record ID.")] = None,
        related_to: Annotated[
            list[str] | None, Field(description="Related record IDs.")
        ] = None,
        type: Annotated[
            str | None,
            Field(description="Record role, e.g. task, experiment, dataset."),
        ] = None,
        commit: Annotated[
            str | None,
            Field(description="Git ref to bind; resolved to a full hash."),
        ] = None,
        markdown: Annotated[str, Field(description="Markdown body.")] = "",
        fields: Annotated[
            dict[str, Any] | None, Field(description="Additional stored fields.")
        ] = None,
        activate: Annotated[
            bool, Field(description="Make the new record the active record.")
        ] = False,
    ) -> dict[str, Any]:
        config = project()
        extra = dict(fields or {})
        reserved = {"id", "parent_id", "related_to", "markdown", "type", "commit"}
        if reserved.intersection(extra):
            raise ValidationError(
                f"Use the dedicated arguments for {sorted(reserved)}, not fields"
            )
        if type is not None:
            extra["type"] = type
        if commit is not None:
            extra["commit"] = GitRepository(config).resolve(commit)
        record = Record.create(
            record_id,
            parent_id=parent_id,
            related_to=list(related_to or []),
            markdown=markdown,
            activate=activate,
            config=config,
            **extra,
        )
        return {"created": record.id, "path": str(record.path)}

    def record_update(
        record_id: Annotated[str, Field(description="Record to update.")],
        fields: Annotated[
            dict[str, Any] | None, Field(description="Stored fields to set.")
        ] = None,
        unset: Annotated[
            list[str] | None, Field(description="Stored fields to remove.")
        ] = None,
        parent_id: Annotated[
            str | None, Field(description="New parent record ID.")
        ] = None,
        make_root: Annotated[
            bool, Field(description="Make the record a root.")
        ] = False,
        related_to: Annotated[
            list[str] | None,
            Field(description="Replacement list of related record IDs."),
        ] = None,
        commit: Annotated[
            str | None,
            Field(description="Git ref to bind; resolved to a full hash."),
        ] = None,
        markdown: Annotated[
            str | None, Field(description="Replacement Markdown body.")
        ] = None,
    ) -> dict[str, Any]:
        config = project()
        changes = dict(fields or {})
        if related_to is not None:
            changes["related_to"] = list(related_to)
        if commit is not None:
            changes["commit"] = GitRepository(config).resolve(commit)
        record = update_record(
            record_id,
            changes=changes,
            unset=list(unset or []),
            parent_id=parent_id,
            make_root=make_root,
            markdown=markdown,
            config=config,
        )
        return {"updated": record.id}

    def runs(
        record_id: Annotated[
            str | None,
            Field(description="Record whose runs to list; defaults to active."),
        ] = None,
        all: Annotated[
            bool,
            Field(description="List every attributed run in the record's source."),
        ] = False,
        sort: Annotated[
            str | None, Field(description="Field or metric to sort by.")
        ] = None,
        ascending: Annotated[
            bool | None,
            Field(description="Sort direction; defaults to the metric direction."),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=200, description="Maximum runs returned.")
        ] = DEFAULT_RUN_LIMIT,
    ) -> list[dict[str, Any]]:
        config = project()
        record = _active_record(record_id, config)
        query: dict[str, Any] = {"limit": limit}
        if sort is not None:
            query["sort"] = sort
            if ascending is not None:
                query["ascending"] = ascending
            else:
                declarations = record.resolve(config=config).values.get("metrics") or []
                directions = {item.name: item.direction for item in declarations}
                query["ascending"] = directions.get(sort) != "maximize"
        elif ascending is not None:
            raise ValidationError("ascending requires sort")
        found = record.resolve_tracking_backend(config=config).list_runs(
            record_id=None if all else record.id, **query
        )
        # Runs keep their fields in read-only mappings.
        return to_jsonable_python(found, fallback=dict)

    add_tool(
        record_list,
        "List research records with their parent, type, status, and decision.",
        read_only=True,
    )
    add_tool(
        record_show,
        "Show a record's effective fields with the records defining them, "
        "including resolved entrypoints.",
        read_only=True,
    )
    add_tool(record_create, "Create a root or child research record.", read_only=False)
    add_tool(
        record_update,
        "Update a record's stored fields, parent, links, commit, or body.",
        read_only=False,
    )
    add_tool(runs, "List tracked runs attributed to a record.", read_only=True)


def _active_record(record_id: str | None, config: HirakuConfig) -> Record:
    identifier = record_id or config.active_record_id
    if identifier is None:
        raise NoActiveRecordError("No active record; pass record_id")
    return load_record(identifier, config=config)


# Resources and helpers --------------------------------------------------------


def _register_resources(
    server: MCPServer, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    @server.resource(
        "hiraku://records/{record_id}",
        name="record",
        description="Full Markdown file of a research record",
        mime_type="text/markdown",
    )
    def record(record_id: str) -> str:
        return _read(_resource(load_record, record_id, config=project()).path)

    @server.resource(
        "hiraku://sessions/{session_id}",
        name="session",
        description="Full stored state of a workflow session",
        mime_type="application/json",
    )
    def session(session_id: str) -> str:
        stored = _resource(Session.load, session_id, config=project())
        return _dumps(_show(stored, workflows))

    @server.resource(
        "hiraku://workflows/{workflow_id}",
        name="workflow",
        description="Prompt document of a workflow",
        mime_type="text/markdown",
    )
    def workflow(workflow_id: str) -> str:
        if workflow_id not in workflows:
            raise ResourceError(f"Unknown workflow {workflow_id!r}")
        return workflows[workflow_id].text

    @server.resource(
        "hiraku://contexts/{context_id}",
        name="context",
        description="Curated project context",
        mime_type="text/markdown",
    )
    def context(context_id: str) -> str:
        return _read(_resource(load_context, context_id, config=project()).path)


def _json_result(function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function to return JSON text and Hiraku errors as tool errors.

    The wrapper keeps the function's signature, which defines the tool schema.
    """

    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            return _text(_dumps(function(*args, **kwargs)))
        except (HirakuError, PydanticValidationError) as exc:
            return _text(str(exc), error=True)

    return run


def _resource(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except HirakuError as exc:
        raise ResourceError(str(exc)) from exc


def _read(path: Path | None) -> str:
    assert path is not None
    return path.read_text(encoding="utf-8")


def _text(text: str, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _version() -> str:
    try:
        return version("hiraku")
    except PackageNotFoundError:  # pragma: no cover - source checkouts
        return "0"
