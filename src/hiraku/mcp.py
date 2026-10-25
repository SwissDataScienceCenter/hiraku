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
import logging
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
from mcp.types import (
    CallToolResult,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .base import BaseRecord
from .config import HirakuConfig
from .errors import HirakuError, ValidationError, WorkflowError
from .execution import execute_command
from .record import Record, list_records, load_record
from .workflows.session import (
    ClientInfo,
    Session,
    list_sessions,
    load_session,
)
from .workflows.workflow import (
    Instruction,
    Workflow,
    list_workflows,
    summary,
)

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, and runs workflows that guide you step by step.
Run commands with execute; it waits for the command and returns its output and
exit code. Start a workflow with the tool named after it
(workflow_list lists them), and answer its instructions by sending events with
workflow_send_event, as its description explains. Use session_list and
session_show to resume earlier sessions.\
"""

EVENT_GUIDE = """\
Send an event to the current instruction of a Hiraku workflow session.

An instruction has text to carry out, inline data, research context, and,
unless it is final, an events block that says which events it accepts:
1. Read attached research resources, then carry out the text using its data.
   Fetch resource links before using them.
2. Follow any requests for user approval in the instruction text before
   sending an event. Approval is a conversational contract.
3. Call this tool with one of events.accepted as event, its arguments as the
   event's schema describes, and the events block's session_id and
   instruction_id unchanged. Answering in the chat does not reach the workflow.
4. An accepted event returns the next instruction. An instruction without an
   events block is final: carry it out, then stop.
5. A response with status rejected or not_ready keeps the same instruction
   current: fix the input, or check again later for not_ready. Status stale
   means the event answered an instruction that is no longer current; continue
   from the current instruction in the response.
6. Resubmitting an answered instruction returns stale and the current instruction;
   it never applies the event twice. Continue from that current instruction.
7. To resume, call session_show with the session_id; it returns the current
   instruction. Do not start a new session to resume an existing one.
Before repeating consequential work such as creating records or launching
experiments, check whether it already exists.\
"""

Project = Callable[[], HirakuConfig]
SessionId = Annotated[str, Field(description="Session ID from session_list.")]
ClientInfoInput = Annotated[
    ClientInfo | None,
    Field(
        description="Injected by the client hook. Omit this field when calling "
        "the tool; never invent client or session identifiers."
    ),
]
logger = logging.getLogger(__name__)


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
    def execute(
        record_id: str,
        command: list[str],
        dispatch: str | None = None,
    ) -> dict[str, Any]:
        """Run a command in the foreground and return its output and exit code."""
        result = execute_command(
            command,
            record_id=record_id,
            dispatch=dispatch,
            capture_output=True,
            config=selected,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
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
        """Discover records and attached source files."""
        uris = [f"hiraku://records/{item.id}" for item in list_records(config=selected)]
        for record in list_records(config=selected):
            root = (selected.records_dir / record.path.parent.name).resolve()
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.resolve().is_relative_to(root):
                    relative = path.relative_to(root).as_posix()
                    uris.append(
                        f"hiraku://sources/{record.id}/{quote(relative, safe='')}"
                    )
        return json.dumps(uris)

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return _research_content("records", record_id, selected)

    @server.resource("hiraku://sources/{record_id}/{source_path}")
    def source_resource(record_id: str, source_path: str) -> bytes:
        record = load_record(record_id, config=selected)
        root = (selected.records_dir / record.path.parent.name).resolve()
        path = (root / unquote(source_path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be a file inside its record directory")
        return path.read_bytes()

    @server.prompt()
    def propose_implementation(record_id: str, related_record_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated related records."""
        record = load_record(record_id, config=selected)
        related = [
            load_record(identifier.strip(), config=selected)
            for identifier in related_record_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in related)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Inspect attached sources "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(_data(record))}\n\nRelated records:\n{evidence}"
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

    workflows = {}
    for workflow in list_workflows(config=config):
        if "hiraku_client" in workflow.graph.start.arguments.model_fields:
            logger.warning(
                "Workflow %s is unavailable over MCP: start argument "
                "'hiraku_client' is reserved",
                workflow.id,
            )
            continue
        workflows[workflow.id] = workflow

    def render(response: Mapping[str, Any]) -> CallToolResult:
        return _instruction_result(response, project())

    _add_workflow_tools(server, project, workflows, render)
    _add_session_tools(server, project, workflows, render)
    for workflow_id, workflow in sorted(workflows.items()):
        server.add_prompt(
            Prompt.from_function(
                _start_prompt(workflow),
                name=workflow_id,
                description=summary(workflow.description)
                or f"Start the {workflow_id} workflow.",
            )
        )
    _register_resources(server, project, workflows)


# Workflows and sessions -------------------------------------------------------


def _add_workflow_tools(
    server: MCPServer,
    project: Project,
    workflows: Mapping[str, Workflow],
    render: Callable[[Mapping[str, Any]], CallToolResult],
) -> None:
    def workflow_list() -> list[dict[str, Any]]:
        return [
            {"id": workflow.id, "description": workflow.description}
            for _, workflow in sorted(workflows.items())
        ]

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
        hiraku_client: ClientInfoInput = None,
    ) -> CallToolResult:
        config = project()
        if session_id is None:
            active = [
                s.id for s in list_sessions(config=config) if s.status == "active"
            ]
            if len(active) != 1:
                return render(
                    {
                        "status": "rejected",
                        "message": (
                            f"Several sessions are active ({', '.join(active)}); pass "
                            "session_id"
                            if active
                            else "No session is active; start a workflow first"
                        ),
                    }
                )
            session_id = active[0]
        session = load_session(session_id, config=config)
        workflow = workflows.get(session.workflow)
        if workflow is None:
            raise ToolError("Workflow unavailable; reconnect after fixing it")
        with session.lock():
            result = session.run_event(
                workflow,
                instruction_id,
                event,
                arguments,
                client_info=hiraku_client,
            )
        return render(
            _over_mcp(
                {
                    **session.get_instruction(workflow),
                    **result.model_dump(exclude_none=True),
                }
            )
        )

    server.add_tool(
        workflow_list,
        description=(
            "List the project's workflows. Start one with the tool named after it."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_send_event,
        description=EVENT_GUIDE,
        annotations=ToolAnnotations(read_only_hint=False),
    )
    for workflow_id, workflow in sorted(workflows.items()):
        server.add_tool(
            _start_tool(workflow, render),
            description=_start_description(workflow),
            annotations=ToolAnnotations(read_only_hint=False),
            name=workflow_id,
        )


def _add_session_tools(
    server: MCPServer,
    project: Project,
    workflows: Mapping[str, Workflow],
    render: Callable[[Mapping[str, Any]], CallToolResult],
) -> None:
    def session_list() -> list[dict[str, Any]]:
        return [
            {
                "session_id": s.id,
                "workflow": s.workflow,
                "status": s.status,
                "node": s.state.node,
                "instruction_id": s.instruction_id,
                "updated_at": s.updated_at.isoformat(),
            }
            for s in list_sessions(config=project())
        ]

    def session_show(session_id: SessionId) -> CallToolResult:
        session = load_session(session_id, config=project())
        return render(_show(session, workflows))

    def session_cancel(session_id: SessionId) -> dict[str, Any]:
        session = load_session(session_id, config=project())
        with session.lock():
            return session.cancel()

    server.add_tool(
        session_list,
        description="List workflow sessions with their status, newest first.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        session_show,
        description=(
            "Show a workflow session: pending instruction or result, and "
            "stored data. Use it to resume a session."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        session_cancel,
        description="Cancel a workflow session. Records, code, and runs are kept.",
        annotations=ToolAnnotations(read_only_hint=False),
    )


def _show(session: Session, workflows: Mapping[str, Workflow]) -> dict[str, Any]:
    return _over_mcp(session.show(workflows.get(session.workflow)))


def _over_mcp(response: Mapping[str, Any]) -> dict[str, Any]:
    """Name the tool that sends events in an instruction's events block.

    The runtime does not know the transport; over MCP, events go through
    workflow_send_event; the core instruction remains transport independent.
    """
    instruction = response.get("instruction")
    if not isinstance(instruction, Mapping) or "events" not in instruction:
        return dict(response)
    events = {"tool": "workflow_send_event", **instruction["events"]}
    return {**response, "instruction": {**instruction, "events": events}}


def _start_tool(
    workflow: Workflow,
    render: Callable[[Mapping[str, Any]], CallToolResult],
) -> Callable[..., Any]:
    """Return a function whose signature is the workflow's start arguments."""

    def run(**arguments: Any) -> CallToolResult:
        client_info = arguments.pop("hiraku_client", None)
        session = Session.start(workflow, arguments, client_info=client_info)
        return render(_over_mcp(session.get_instruction(workflow)))

    parameters = []
    for name, field in workflow.graph.start.arguments.model_fields.items():
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
    parameters.append(
        inspect.Parameter(
            "hiraku_client",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=ClientInfoInput,
        )
    )
    return _with_signature(run, workflow.id, parameters)


def _start_prompt(workflow: Workflow) -> Callable[..., str]:
    """Return a function that starts a session and renders its instruction.

    Prompt arguments are strings; the start arguments' model converts them.
    """

    def run(**arguments: Any) -> str:
        values = {key: value for key, value in arguments.items() if value is not None}
        try:
            model = workflow.graph.start.arguments
            inputs = model.from_text(values).model_dump(mode="json")
            session = Session.start(workflow, inputs)
            response = _over_mcp(session.get_instruction(workflow))
        except Exception as exc:
            if not isinstance(exc, (HirakuError, PydanticValidationError)):
                logger.exception("Workflow %s prompt failed", workflow.id)
            # The SDK hides exception messages from prompts, so explain in text.
            return (
                f"The Hiraku workflow `{workflow.id}` did not start: "
                f"{exc}. Tell the user and ask for corrected "
                "arguments."
            )
        instruction = response["instruction"]
        text = Instruction.model_validate(instruction).to_text()
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
        for name, field in workflow.graph.start.arguments.model_fields.items()
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
    if text := summary(workflow.description):
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
        stored = load_session(session_id, config=project())
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
        return str(workflows[workflow_id].markdown).strip()

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
            return workflows[workflow_id].graph.to_mermaid()
        except WorkflowError as exc:
            raise ResourceError(str(exc)) from exc


def _research_content(kind: str, identifier: str, config: HirakuConfig) -> str:
    """Read the same live representation for resources and tool attachments."""
    return json.dumps(_data(load_record(identifier, config=config)))


def _instruction_result(
    response: Mapping[str, Any], config: HirakuConfig
) -> CallToolResult:
    """Attach native research resources without changing the stored instruction."""
    content: list[Any] = [
        TextContent(type="text", text=json.dumps(dict(response), ensure_ascii=False))
    ]
    instruction = response.get("instruction")
    context = instruction.get("context") if isinstance(instruction, Mapping) else None
    for kind, identifiers in (context or {}).items():
        mime = "application/json"
        for identifier in identifiers:
            uri = f"hiraku://{kind}/{quote(identifier, safe='')}"
            link = ResourceLink(
                type="resource_link", uri=uri, name=identifier, mime_type=mime
            )
            content.append(link)
            try:
                _research_content(kind, identifier, config)
            except (HirakuError, PydanticValidationError) as exc:
                content.append(
                    TextContent(type="text", text=f"Resource {uri} unavailable: {exc}")
                )
    return CallToolResult(content=content, structured_content=dict(response))
