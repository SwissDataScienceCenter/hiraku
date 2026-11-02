"""Optional local MCP interface to a single Hiraku project."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import (
    ResourceError,
    ToolError,
    UnexpectedResourceError,
    UnexpectedToolError,
)
from mcp.types import (
    ToolAnnotations,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .config import HirakuConfig
from .errors import HirakuError, ValidationError
from .execution import execute_command
from .record import Record, list_records, open_workspace, query_records
from .workflows.langgraph import resume_workflow as resume_langgraph_workflow
from .workflows.langgraph import start_workflow as start_langgraph_workflow
from .workflows.langgraph import workflow_details as langgraph_workflow_details
from .workflows.langgraph import workflow_ids, workflow_mermaid
from .workflows.langgraph import workflow_status as langgraph_workflow_status
from .workflows.session_history import list_sessions, session_history

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, runs commands with execute, and runs LangGraph
workflows. Use workflow_start, workflow_status, and workflow_resume to operate
a workflow. When an interrupt requests approval, workflow_approval opens a
structured MCP elicitation and waits for the user's answer.
Use workflow_sessions and workflow_session_history to inspect recorded
sessions and their history.
"""


class WorkflowApproval(BaseModel):
    model_config = ConfigDict(
        json_schema_extra=lambda schema: schema.pop("title", None)
    )

    decision: Literal["approve", "revise", "stop"] = Field(
        description="Whether to approve, request a revision, or stop."
    )
    feedback: str = Field(default="", description="Optional revision feedback.")


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
        return contents


def _data(record: Record) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def create_server(config: HirakuConfig | None = None) -> Any:
    """Create a stdio-capable server bound to a discovered or supplied project.

    The MCP SDK is a regular Hiraku dependency. Resources are read afresh on
    each request so external edits and tool mutations remain visible.
    """
    selected = config or HirakuConfig.discover()

    server = ProjectMCP("Hiraku", instructions=INSTRUCTIONS)

    @server.tool()
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        return [_data(item) for item in list_records(open_workspace(selected))]

    @server.tool()
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record's own fields."""
        return _data(Record.load(open_workspace(selected), record_id))

    @server.tool()
    def record_query(query: str) -> dict[str, Any]:
        """Run a read-only fmql query over knowledge-base packets."""

        result = query_records(open_workspace(selected), query)
        return {"columns": list(result.columns), "rows": list(result.rows)}

    @server.tool()
    def record_create(
        record_id: str,
        path: str,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        extras = fields or {}
        workspace = open_workspace(selected)
        extras = {
            **extras,
            "id": record_id,
            "parent_id": parent_id,
            "related_to": related_to or [],
            "markdown": markdown,
        }
        record = Record.create(workspace, path, **extras)
        if activate:
            selected.set_active_record(record)
        return _data(record)

    @server.tool()
    def record_update(
        record_id: str,
        fields: dict[str, Any],
        markdown: str | None = None,
    ) -> dict[str, Any]:
        """Merge record fields and optionally replace Markdown; null clears a field."""
        workspace = open_workspace(selected)
        record = Record.load(workspace, record_id)
        return _data(record.update(workspace, fields, markdown=markdown))

    @server.tool()
    def record_delete(record_id: str) -> dict[str, str]:
        """Delete a record using Hiraku's relationship checks."""
        workspace = open_workspace(selected)
        Record.load(workspace, record_id).delete(workspace)
        return {"deleted": record_id}

    @server.tool()
    def execute(
        record_id: str,
        command: list[str],
        dispatch: str | None = None,
    ) -> dict[str, Any]:
        """Run a command in the foreground and return output and exit code."""
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
        record = Record.load(open_workspace(selected), record_id)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = record.metrics or []
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
        """Discover current record resources."""
        records = list_records(open_workspace(selected))
        uris = [f"hiraku://records/{item.id}" for item in records]
        return json.dumps(uris)

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return _research_content(record_id, selected)

    @server.prompt()
    def propose_implementation(record_id: str, related_record_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated related records."""
        record = Record.load(open_workspace(selected), record_id)
        related = [
            Record.load(open_workspace(selected), identifier.strip())
            for identifier in related_record_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in related)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Inspect related records "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(_data(record))}\n\nRelated records:\n{evidence}"
        )

    add_workflow_tools(server, selected)
    return server


def add_workflow_tools(server: MCPServer, config: HirakuConfig) -> None:
    """Add LangGraph workflow tools and definitions to a server."""
    path = config.path

    def pending_approval(
        workflow_session_id: str, interrupt_id: str | None = None
    ) -> dict[str, Any]:
        status = langgraph_workflow_status(workflow_session_id, config=project())
        interrupts = status["interrupts"]
        if status["status"] != "waiting" or len(interrupts) != 1:
            raise ValidationError("Workflow must have exactly one pending interrupt")
        pending = interrupts[0]
        if interrupt_id is not None and pending["id"] != interrupt_id:
            raise ValidationError("Workflow interrupt has changed")
        value = pending["value"]
        if not isinstance(value, dict) or not isinstance(value.get("approval"), dict):
            raise ValidationError(
                "The current workflow interrupt does not request approval"
            )
        return pending

    def project() -> HirakuConfig:
        return HirakuConfig.load(path)

    def workflow_list() -> list[dict[str, Any]]:
        return langgraph_workflow_details(config=project())

    def workflow_start(
        workflow_id: Annotated[str, Field(description="Project workflow ID.")],
        arguments: Annotated[
            dict[str, Any] | None, Field(description="Workflow input object.")
        ] = None,
        context: Annotated[
            dict[str, Any] | None,
            Field(description="Optional caller-provided session context."),
        ] = None,
    ) -> dict[str, Any]:
        return start_langgraph_workflow(
            workflow_id, arguments, config=project(), context=context
        )

    def workflow_sessions() -> list[dict[str, Any]]:
        return list_sessions(config=project())

    def workflow_session_history(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> dict[str, Any]:
        return session_history(workflow_session_id, config=project())

    def workflow_status(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> dict[str, Any]:
        return langgraph_workflow_status(workflow_session_id, config=project())

    def workflow_resume(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
        response: Annotated[
            dict[str, Any],
            Field(description="Response to the current graph interrupt."),
        ],
        checkpoint_id: Annotated[
            str, Field(description="Checkpoint ID from workflow status.")
        ],
        context: Annotated[
            dict[str, Any] | None,
            Field(description="Optional caller-provided transition context."),
        ] = None,
    ) -> dict[str, Any]:
        return resume_langgraph_workflow(
            workflow_session_id,
            response,
            config=project(),
            checkpoint_id=checkpoint_id,
            context=context,
        )

    def resolve_workflow_approval(
        workflow_session_id: str,
        interrupt_id: str,
        proposal: str,
        response: dict[str, Any],
    ) -> Elicit[WorkflowApproval]:
        pending = pending_approval(workflow_session_id, interrupt_id)
        approval = pending["value"]["approval"]
        return Elicit(
            message=(
                f"{approval.get('message', 'Review the proposed workflow action.')}\n\n"
                f"{proposal}\n\nWorkflow response: "
                f"{json.dumps(response, sort_keys=True)}"
                "\n\nChoose approve, revise, or stop."
            ),
            schema=WorkflowApproval,
        )

    def workflow_approval(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
        proposal: Annotated[
            str, Field(description="Concise proposal or result to show the user.")
        ],
        interrupt_id: Annotated[str, Field(description="Current interrupt ID.")],
        response: Annotated[dict[str, Any], Field(description="Response to approve.")],
        approval: Annotated[
            ElicitationResult[WorkflowApproval], Resolve(resolve_workflow_approval)
        ],
    ) -> dict[str, Any]:
        """Ask the user to resolve an approval request in the current interrupt."""
        if approval.action == "accept":
            pending_approval(workflow_session_id, interrupt_id)
            result = {
                "approval": {
                    "decision": approval.data.decision,
                    **(
                        {"feedback": approval.data.feedback}
                        if approval.data.feedback
                        else {}
                    ),
                }
            }
            if approval.data.decision == "approve":
                result["response"] = response
            return result
        return {"decision": approval.action}

    workflow_approval.__annotations__["approval"] = Annotated[
        ElicitationResult[WorkflowApproval], Resolve(resolve_workflow_approval)
    ]

    server.add_tool(
        workflow_list,
        description="List project LangGraph workflows.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_start,
        description=(
            "Start a LangGraph workflow and return its interrupt or final result."
        ),
        annotations=ToolAnnotations(read_only_hint=False),
    )
    server.add_tool(
        workflow_sessions,
        description="List recorded workflow sessions and their metadata.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_session_history,
        description="Read a workflow session's recorded metadata and history.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_status,
        description=(
            "Inspect a checkpointed LangGraph workflow run without advancing it."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_resume,
        description="Resume a paused LangGraph run with the response to its interrupt.",
        annotations=ToolAnnotations(read_only_hint=False),
    )
    server.add_tool(
        workflow_approval,
        description=(
            "Open a structured MCP approval prompt when the current workflow "
            "interrupt declares an approval request. Supply the current interrupt "
            "ID and proposed response; pass its result to workflow_resume."
        ),
        annotations=ToolAnnotations(read_only_hint=False),
    )

    @server.resource(
        "hiraku://workflows/{workflow_id}",
        name="workflow",
        description="Markdown definition for a project LangGraph workflow",
        mime_type="text/markdown",
    )
    def workflow_resource(workflow_id: str) -> str:
        current = project()
        if workflow_id not in workflow_ids(config=current):
            raise ResourceError(f"Unknown workflow {workflow_id!r}")
        markdown_path = current.workflows_dir / workflow_id / "workflow.md"
        if not markdown_path.is_file():
            return ""
        return markdown_path.read_text(encoding="utf-8").strip()

    @server.resource(
        "hiraku://workflows/{workflow_id}/graph",
        name="workflow_graph",
        description="LangGraph workflow topology as Mermaid text",
        mime_type="text/vnd.mermaid",
    )
    def workflow_graph_resource(workflow_id: str) -> str:
        return workflow_mermaid(workflow_id, config=project())


def _research_content(identifier: str, config: HirakuConfig) -> str:
    """Read the same live representation for resources and tool attachments."""
    return json.dumps(_data(Record.load(open_workspace(config), identifier)))
