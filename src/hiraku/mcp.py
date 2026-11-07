"""Optional local MCP interface to a single Hiraku project."""

from __future__ import annotations

import importlib.metadata
import json
import mimetypes
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote

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

from .errors import HirakuError, ValidationError
from .execution import execute_command
from .project import Project
from .record import Record
from .tracking import TrackingBackendFactory
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
        # Source templates contain many file types; MIME depends on the URI.
        if str(uri).startswith("hiraku://sources/"):
            mime = mimetypes.guess_type(unquote(str(uri)))[0]
            for item in contents:
                item.mime_type = mime or "application/octet-stream"
        return contents


def _data(record: Record) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def _update(
    project: Project, record: Record, fields: dict[str, Any], markdown: str | None
) -> Record:
    protected = {"id", "path", "markdown", "parent_id"}
    invalid = protected.intersection(fields)
    if invalid:
        raise ValidationError(f"Cannot update fields: {sorted(invalid)}")
    values = {
        **record.model_dump(exclude_unset=True, exclude={"path"}),
        **fields,
    }
    if markdown is not None:
        values["markdown"] = markdown
    updated = type(record).model_validate(values)
    updated._path = record.path
    updated._persisted_id = record._persisted_id
    if "related_to" in fields:
        project.vault.validate_relationships(updated)
    project.vault.save(updated)
    return updated


def create_server(project: Project | None = None) -> ProjectMCP:
    """Create a stdio-capable server bound to a discovered or supplied project.

    The MCP SDK is a regular Hiraku dependency. Resources are read afresh on
    each request so external edits and tool mutations remain visible.
    """
    selected = project or Project.discover()

    server = ProjectMCP(
        name="Hiraku",
        title="Hiraku MCP Server",
        instructions=INSTRUCTIONS,
        description=INSTRUCTIONS,
        version=importlib.metadata.version("hiraku"),
    )
    register_tools(server, selected)
    register_workflow_tools(server, selected)
    return server


def register_tools(server: MCPServer, selected: Project) -> None:
    """Register the project, record, execution, and tracking capabilities.

    Tools: project_info, record_list, record_show, record_create, record_update,
    record_delete, execute, and runs. Also registers the record catalog and
    source resources, and the propose_implementation prompt.
    """

    @server.tool(
        title="Project information",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def project_info() -> dict[str, Any]:
        """Read the current project configuration and metadata."""
        return to_jsonable_python(Project.load(selected.path).to_dict(), fallback=dict)

    @server.tool(
        title="List research records",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        return [_data(item) for item in selected.vault.list()]

    @server.tool(
        title="Show research record",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record and its effective inherited fields."""
        record = selected.vault.get(record_id)
        resolved = selected.vault.resolve(record.id)
        return {
            **_data(record),
            "resolved": resolved["values"],
            "sources": resolved["sources"],
        }

    @server.tool(
        title="Create research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def record_create(
        name: str,
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
            "name",
            "path",
            "parent_id",
            "related_to",
            "markdown",
            "record_id",
            "activate",
            "project",
        }
        if reserved.intersection(extras):
            raise ValidationError("Use named arguments for identity and relationships")
        record = selected.vault.create(
            name,
            parent_id=parent_id,
            markdown=markdown,
            related_to=related_to or [],
            **extras,
        )
        if activate:
            selected.set_active_record(record)
        return _data(record)

    @server.tool(
        title="Update research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def record_update(
        record_id: str,
        fields: dict[str, Any],
        markdown: str | None = None,
    ) -> dict[str, Any]:
        """Merge record fields and optionally replace Markdown; null clears a field."""
        return _data(_update(selected, selected.vault.get(record_id), fields, markdown))

    @server.tool(
        title="Delete research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def record_delete(record_id: str) -> dict[str, str]:
        """Delete a record using Hiraku's relationship checks."""
        selected.vault.delete(selected.vault.get(record_id).id)
        return {"deleted": record_id}

    @server.tool(
        title="Execute project command",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
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
            project=selected,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }

    @server.tool(
        title="List tracked runs",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
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
        record = selected.vault.get(record_id)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = (
                selected.vault.resolve(record.id)["values"].get("metrics") or []
            )
            directions = {item["name"]: item["direction"] for item in declarations}
            query["sort"] = sort
            query["ascending"] = (
                ascending
                if ascending is not None
                else directions.get(sort) != "maximize"
            )
        found = (
            TrackingBackendFactory(selected)
            .create(selected.tracking_backend)
            .list_runs(record_id=None if all_records else record.id, **query)
        )
        # Runs keep their fields in read-only mappings.
        return to_jsonable_python(found, fallback=dict)

    @server.resource("hiraku://catalog", mime_type="application/json")
    def catalog() -> str:
        """Discover current records and attached source files."""
        records = selected.vault.list()
        uris = [f"hiraku://records/{item.id}" for item in records]
        for record in records:
            root = (
                record.path.parent
                if record.path.parent != selected.records_dir
                else selected.records_dir / record.path.stem
            ).resolve()
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.resolve().is_relative_to(root):
                    relative = path.relative_to(root).as_posix()
                    uris.append(
                        f"hiraku://sources/{record.id}/{quote(relative, safe='')}"
                    )
        return json.dumps(uris)

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return _research_content(record_id, selected)

    @server.resource("hiraku://sources/{record_id}/{source_path}")
    def source_resource(record_id: str, source_path: str) -> bytes:
        record = selected.vault.get(record_id)
        root = (
            record.path.parent
            if record.path.parent != selected.records_dir
            else selected.records_dir / record.path.stem
        ).resolve()
        path = (root / unquote(source_path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be a file inside its record directory")
        return path.read_bytes()

    @server.prompt(title="Propose implementation")
    def propose_implementation(record_id: str, related_record_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated related records."""
        record = selected.vault.get(record_id)
        related = [
            selected.vault.get(identifier.strip())
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


def register_workflow_tools(server: MCPServer, project: Project) -> None:
    """Register workflow discovery, execution, sessions, and approval.

    Tools: workflow_list, workflow_start, workflow_sessions,
    workflow_session_history, workflow_status, workflow_resume, and
    workflow_approval. Also registers workflow definition and graph resources.
    """
    path = project.path

    def pending_approval(
        workflow_session_id: str, interrupt_id: str | None = None
    ) -> dict[str, Any]:
        status = langgraph_workflow_status(workflow_session_id, project=project())
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

    def project() -> Project:
        return Project.load(path)

    def workflow_list() -> list[dict[str, Any]]:
        return langgraph_workflow_details(project=project())

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
            workflow_id, arguments, project=project(), context=context
        )

    def workflow_sessions() -> list[dict[str, Any]]:
        return list_sessions(project=project())

    def workflow_session_history(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> dict[str, Any]:
        return session_history(workflow_session_id, project=project())

    def workflow_status(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> dict[str, Any]:
        return langgraph_workflow_status(workflow_session_id, project=project())

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
            project=project(),
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
        title="List workflows",
        description="List project LangGraph workflows.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_start,
        title="Start workflow",
        description=(
            "Start a LangGraph workflow and return its interrupt or final result."
        ),
        annotations=ToolAnnotations(read_only_hint=False),
    )
    server.add_tool(
        workflow_sessions,
        title="List workflow sessions",
        description="List recorded workflow sessions and their metadata.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_session_history,
        title="Read workflow session history",
        description="Read a workflow session's recorded metadata and history.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_status,
        title="Check workflow status",
        description=(
            "Inspect a checkpointed LangGraph workflow run without advancing it."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    server.add_tool(
        workflow_resume,
        title="Resume workflow",
        description="Resume a paused LangGraph run with the response to its interrupt.",
        annotations=ToolAnnotations(read_only_hint=False),
    )
    server.add_tool(
        workflow_approval,
        title="Request workflow approval",
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
        if workflow_id not in workflow_ids(project=current):
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
        return workflow_mermaid(workflow_id, project=project())


def _research_content(identifier: str, project: Project) -> str:
    """Read the same live representation for resources and tool attachments."""
    return json.dumps(_data(project.vault.get(identifier)))
