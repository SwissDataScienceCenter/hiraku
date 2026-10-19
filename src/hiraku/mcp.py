"""Optional local MCP interface to a single Hiraku project.

Workflows add their tools, prompts, and resources with `add_workflow_tools`.
They are fixed when the server starts, because coding agents do not reliably
pick up changes to the tool list. Each workflow is started by a tool and a
prompt named after it, and every event goes through one
``workflow_send_event`` tool.
"""

from __future__ import annotations

import inspect
import json
import mimetypes
from collections.abc import Callable, Mapping
from typing import Annotated, Any
from urllib.parse import quote, unquote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import (
    ResourceError,
    ToolError,
    UnexpectedResourceError,
    UnexpectedToolError,
)
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .base import BaseRecord
from .config import HirakuConfig
from .context import Context, list_contexts, load_context
from .errors import HirakuError, ValidationError, WorkflowError
from .launch import launch, render_arguments
from .record import Record, list_records, load_record
from .session import Session
from .workflow import (
    Workflow,
    instruction_text,
    list_workflows,
    load_workflows,
    summary,
    workflow_description,
    workflow_graph,
)

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, and runs workflows that guide you step by step.
Start a workflow with the tool named after it (workflow_list lists them), and
answer its instructions by sending events with workflow_send_event, as its
description explains. Use session_list and session_show to resume earlier
sessions.\
"""

EVENT_GUIDE = """\
Send an event to the current instruction of a Hiraku workflow session.

An instruction has text to carry out, context with the data it refers to, and,
unless it is final, an events block that says which events it accepts:
1. Carry out the instruction text, using its context.
2. If events.requires_user_response is true, ask the user and wait for their
   answer before sending an event; otherwise decide yourself.
3. Call this tool with one of events.accepted as event, its arguments as the
   event's schema describes, and the events block's session_id and
   instruction_id unchanged. Answering in the chat does not reach the workflow.
4. An accepted event returns the next instruction. An instruction without an
   events block is final: carry it out, then stop.
5. A response with status rejected or not_ready keeps the same instruction
   current: fix the input, or check again later for not_ready. Status stale
   means the event answered an instruction that is no longer current; continue
   from the current instruction in the response.
6. Sending an identical event again is safe; it returns the same response.
7. To resume, call session_show with the session_id; it returns the current
   instruction. Do not start a new session to resume an existing one.
Before repeating consequential work such as creating records or launching
experiments, check whether it already exists.\
"""

Project = Callable[[], HirakuConfig]
SessionId = Annotated[str, Field(description="Session ID from session_list.")]


def _data(record: BaseRecord) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def _update(
    record: BaseRecord, fields: dict[str, Any], markdown: str | None
) -> BaseRecord:
    protected = {"id", "path", "markdown", "parent_id"}
    if protected.intersection(fields):
        raise ValidationError(f"Cannot update fields: {sorted(protected)}")
    values = {
        **record.model_dump(exclude_unset=True, exclude={"path"}),
        **fields,
    }
    if markdown is not None:
        values["markdown"] = markdown
    updated = type(record).model_validate(values)
    updated.save(record.path, overwrite=True)
    return updated


def create_server(config: HirakuConfig | None = None) -> Any:
    """Create a stdio-capable server bound to a discovered or supplied project.

    The MCP SDK is a regular Hiraku dependency. Resources are read afresh on
    each request so external edits and tool mutations remain visible.
    """
    selected = config or HirakuConfig.discover()

    class ProjectMCP(MCPServer):
        async def call_tool(
            self, name: str, arguments: dict[str, Any], context: Any = None
        ) -> Any:
            try:
                return await super().call_tool(name, arguments, context)
            except UnexpectedToolError as exc:
                # The SDK hides unexpected messages; Hiraku's validation errors
                # tell the agent how to correct its call.
                cause = exc.__cause__
                if isinstance(cause, HirakuError | PydanticValidationError):
                    raise ToolError(str(cause)) from cause
                raise

        async def read_resource(self, uri: Any, context: Any = None) -> Any:
            try:
                contents = await super().read_resource(uri, context)
            except UnexpectedResourceError as exc:
                # As for tools, keep the reason a Hiraku resource is unavailable.
                if isinstance(exc.__cause__, HirakuError):
                    raise ResourceError(str(exc.__cause__)) from exc.__cause__
                raise
            # Source templates contain many file types; MIME depends on the URI.
            if str(uri).startswith("hiraku://sources/"):
                mime = mimetypes.guess_type(unquote(str(uri)))[0]
                for item in contents:
                    item.mime_type = mime or "application/octet-stream"
            return contents

    server = ProjectMCP("Hiraku", instructions=INSTRUCTIONS)

    @server.tool()
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        return [_data(item) for item in list_records(config=selected)]

    @server.tool()
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record and its effective inherited fields."""
        record = load_record(record_id, config=selected)
        resolved = record.resolve(config=selected)
        return {
            **_data(record),
            "resolved": resolved.values,
            "sources": resolved.sources,
        }

    @server.tool()
    def record_create(
        record_id: str,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        extras = fields or {}
        reserved = {
            "id",
            "path",
            "parent_id",
            "related_to",
            "markdown",
            "record_id",
            "activate",
            "config",
        }
        if reserved.intersection(extras):
            raise ValidationError("Use named arguments for identity and relationships")
        return _data(
            Record.create(
                record_id,
                parent_id=parent_id,
                related_to=related_to,
                markdown=markdown,
                activate=activate,
                config=selected,
                **extras,
            )
        )

    @server.tool()
    def record_update(
        record_id: str,
        fields: dict[str, Any],
        markdown: str | None = None,
    ) -> dict[str, Any]:
        """Merge record fields and optionally replace Markdown; null clears a field."""
        return _data(_update(load_record(record_id, config=selected), fields, markdown))

    @server.tool()
    def record_reparent(record_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Move a record subtree under a parent, or make it a root."""
        record = load_record(record_id, config=selected)
        record.reparent(parent_id, config=selected)
        return _data(record)

    @server.tool()
    def record_delete(record_id: str) -> dict[str, str]:
        """Delete a record using Hiraku's relationship checks."""
        load_record(record_id, config=selected).delete(config=selected)
        return {"deleted": record_id}

    @server.tool()
    def context_list() -> list[dict[str, Any]]:
        """List curated contexts."""
        return [_data(item) for item in list_contexts(config=selected)]

    @server.tool()
    def context_show(context_id: str) -> dict[str, Any]:
        """Read a curated context."""
        return _data(load_context(context_id, config=selected))

    @server.tool()
    def context_create(
        context_id: str, markdown: str = "", fields: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a context with Markdown and optional metadata."""
        extras = fields or {}
        if {"id", "path", "markdown"}.intersection(extras):
            raise ValidationError("Use named arguments for identity and Markdown")
        context = Context(id=context_id, markdown=markdown, **extras)
        context.save(selected.contexts_dir / context.id / "context.md")
        return _data(context)

    @server.tool()
    def context_update(
        context_id: str, fields: dict[str, Any], markdown: str | None = None
    ) -> dict[str, Any]:
        """Merge context metadata and optionally replace its Markdown."""
        return _data(
            _update(load_context(context_id, config=selected), fields, markdown)
        )

    @server.tool()
    def context_delete(context_id: str) -> dict[str, str]:
        """Delete context metadata; attached source files remain on disk."""
        load_context(context_id, config=selected).delete()
        return {"deleted": context_id}

    @server.tool()
    def launch_entrypoint(
        record_id: str,
        entrypoint_name: str,
        args: dict[str, Any] | None = None,
        commit: str | None = None,
        dispatch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Launch a configured entrypoint with attribution, or preview its command."""
        entrypoint = load_record(record_id, config=selected).entrypoint(
            entrypoint_name, config=selected
        )
        if dry_run:
            return {
                "command": [
                    *entrypoint.command,
                    *render_arguments({**entrypoint.args, **(args or {})}),
                ]
            }
        result = launch(
            entrypoint,
            name=entrypoint_name,
            args=args,
            record_id=record_id,
            commit=commit,
            dispatch=dispatch,
            capture_output=True,
            check=False,
            config=selected,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @server.tool()
    def runs(
        record_id: str,
        all_records: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List tracked runs attributed to a record, like `hiraku runs`.

        With all_records, list every attributed run in the record's tracking
        source. Sorting by a declared metric follows its direction unless
        ascending is given.
        """
        if ascending is not None and sort is None:
            raise ValidationError("A sort direction requires sort")
        record = load_record(record_id, config=selected)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = record.resolve(config=selected).values.get("metrics") or []
            directions = {item.name: item.direction for item in declarations}
            query["sort"] = sort
            query["ascending"] = (
                ascending
                if ascending is not None
                else directions.get(sort) != "maximize"
            )
        found = record.resolve_tracking_backend(config=selected).list_runs(
            record_id=None if all_records else record.id, **query
        )
        # Runs keep their fields in read-only mappings.
        return to_jsonable_python(found, fallback=dict)

    @server.resource("hiraku://catalog", mime_type="application/json")
    def catalog() -> str:
        """Discover current records, contexts and source files."""
        uris = [f"hiraku://records/{item.id}" for item in list_records(config=selected)]
        for context in list_contexts(config=selected):
            uris.append(f"hiraku://contexts/{context.id}")
            root = (selected.contexts_dir / context.id).resolve()
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.resolve().is_relative_to(root):
                    relative = path.relative_to(root).as_posix()
                    uris.append(
                        f"hiraku://sources/{context.id}/{quote(relative, safe='')}"
                    )
        return json.dumps(uris)

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return json.dumps(_data(load_record(record_id, config=selected)))

    @server.resource("hiraku://contexts/{context_id}")
    def context_resource(context_id: str) -> str:
        return load_context(context_id, config=selected).markdown

    @server.resource("hiraku://sources/{context_id}/{source_path}")
    def source_resource(context_id: str, source_path: str) -> bytes:
        load_context(context_id, config=selected)
        root = (selected.contexts_dir / context_id).resolve()
        path = (root / unquote(source_path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be a file inside its context directory")
        return path.read_bytes()

    @server.prompt()
    def propose_implementation(record_id: str, context_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated contexts."""
        record = load_record(record_id, config=selected)
        contexts = [
            load_context(identifier.strip(), config=selected)
            for identifier in context_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in contexts)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Inspect attached sources "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(_data(record))}\n\nContexts:\n{evidence}"
        )

    add_workflow_tools(server, selected)
    return server


def add_workflow_tools(server: MCPServer, config: HirakuConfig) -> None:
    """Add the workflow and session tools, prompts, and resources to a server.

    Workflows are loaded once; a workflow added or changed later needs a new
    server, e.g. by reconnecting it in the coding agent. Each call reads the
    project configuration as currently saved, so changes made elsewhere, such
    as a new active record, are visible to a long-running server.
    """
    path = config.path

    def project() -> HirakuConfig:
        return HirakuConfig.load(path)

    workflows = load_workflows(config)

    _add_workflow_tools(server, project, workflows)
    _add_session_tools(server, project, workflows)
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


# Workflows and sessions -------------------------------------------------------


def _add_workflow_tools(
    server: MCPServer, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    def workflow_list() -> list[dict[str, Any]]:
        return list_workflows(workflows)

    def workflow_send_event(
        event: Annotated[
            str, Field(description="One of the events the instruction accepts.")
        ],
        instruction_id: Annotated[
            int, Field(description="instruction_id from the events block.")
        ],
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="The event's arguments, as its schema describes."),
        ] = None,
        session_id: Annotated[
            str | None,
            Field(
                description="session_id from the events block; may be omitted "
                "when only one session is active."
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
            return _over_mcp(
                session.outcome(
                    "rejected",
                    f"Workflow {session.workflow!r} is unavailable; the session "
                    "cannot continue until it loads.",
                )
            )
        return _over_mcp(
            workflow.bind(config).run_event(
                session_id, instruction_id, event, arguments
            )
        )

    _add_tool(
        server,
        workflow_list,
        "List the project's workflows. Start one with the tool named after it.",
        read_only=True,
    )
    _add_tool(
        server,
        workflow_send_event,
        EVENT_GUIDE,
        read_only=False,
    )
    for workflow_id, workflow in sorted(workflows.items()):
        _add_tool(
            server,
            _start_tool(workflow, project),
            _start_description(workflow),
            read_only=False,
            name=workflow_id,
        )


def _add_session_tools(
    server: MCPServer, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    def session_list() -> list[dict[str, Any]]:
        return Session.list_all(config=project())

    def session_show(session_id: SessionId) -> dict[str, Any]:
        return _show(Session.load(session_id, config=project()), workflows)

    def session_cancel(session_id: SessionId) -> dict[str, Any]:
        return Session.load(session_id, config=project()).cancel()

    _add_tool(
        server,
        session_list,
        "List workflow sessions with their status, newest first.",
        read_only=True,
    )
    _add_tool(
        server,
        session_show,
        "Show a workflow session: pending instruction or result, and "
        "stored data. Use it to resume a session.",
        read_only=True,
    )
    _add_tool(
        server,
        session_cancel,
        "Cancel a workflow session. Records, code, and runs are kept.",
        read_only=False,
    )


def _show(session: Session, workflows: Mapping[str, Workflow]) -> dict[str, Any]:
    shown = session.show(workflows.get(session.workflow))
    return {**shown, "response": _over_mcp(shown["response"])}


def _over_mcp(response: Mapping[str, Any]) -> dict[str, Any]:
    """Name the tool that sends events in an instruction's events block.

    The runtime does not know the transport; over MCP, events go through
    workflow_send_event, while hiraku-agent asks for an event file instead.
    """
    instruction = response.get("instruction")
    if not isinstance(instruction, Mapping) or "events" not in instruction:
        return dict(response)
    events = {"tool": "workflow_send_event", **instruction["events"]}
    return {**response, "instruction": {**instruction, "events": events}}


def _start_tool(workflow: Workflow, project: Project) -> Callable[..., Any]:
    """Return a function whose signature is the workflow's start arguments."""

    def run(**arguments: Any) -> dict[str, Any]:
        return _over_mcp(workflow.bind(project()).run_start(arguments))

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
                        field.annotation,  # ty: ignore[invalid-type-form]
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
        instruction = _over_mcp(response)["instruction"]
        text = instruction_text(instruction)
        if "events" not in instruction:
            return text
        # A prompt is a user message: say how to answer, or agents answer in chat.
        return (
            f"{text}\n\nSend one of the accepted events with the "
            "workflow_send_event tool, passing the events block's session_id and "
            "instruction_id, and the event's arguments. Answering in the chat does "
            "not reach the workflow."
        )

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


# Workflow resources and helpers ----------------------------------------------


def _register_resources(
    server: MCPServer, project: Project, workflows: Mapping[str, Workflow]
) -> None:
    @server.resource(
        "hiraku://sessions/{session_id}",
        name="session",
        description="Full stored state of a workflow session",
        mime_type="application/json",
    )
    def session(session_id: str) -> str:
        stored = Session.load(session_id, config=project())
        return json.dumps(_show(stored, workflows), ensure_ascii=False, indent=1)

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
        "hiraku://workflows/{workflow_id}/graph",
        name="workflow_graph",
        description="States and events of a workflow as a Mermaid state diagram",
        mime_type="text/vnd.mermaid",
    )
    def graph(workflow_id: str) -> str:
        if workflow_id not in workflows:
            raise ResourceError(f"Unknown workflow {workflow_id!r}")
        try:
            return workflow_graph(workflows[workflow_id])
        except WorkflowError as exc:
            raise ResourceError(str(exc)) from exc


def _add_tool(
    server: MCPServer,
    function: Callable[..., Any],
    description: str,
    *,
    read_only: bool,
    name: str | None = None,
) -> None:
    server.add_tool(
        function,
        name=name,
        description=description,
        annotations=ToolAnnotations(read_only_hint=read_only),
    )
