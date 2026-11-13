"""Optional local MCP interface to a single Hiraku project."""

from __future__ import annotations

import importlib.metadata
import json
import logging
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote

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
from psij import InvalidJobException, JobAttributes, JobExecutor, SubmitException
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .errors import ExecutionError, HirakuError, ValidationError
from .job import Submission, observe_submission, prepare_job
from .project import Project
from .tracking import Leaderboard
from .workflows.langgraph import resume_workflow as resume_langgraph_workflow
from .workflows.langgraph import start_workflow as start_langgraph_workflow
from .workflows.langgraph import workflow_details as langgraph_workflow_details
from .workflows.langgraph import workflow_ids, workflow_mermaid
from .workflows.langgraph import workflow_status as langgraph_workflow_status

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, submits commands with job_submit, and runs LangGraph
workflows. Use workflow_start, workflow_status, and workflow_resume to operate
a workflow. When an interrupt requests approval, workflow_approval opens a
structured MCP elicitation and waits for the user's answer.
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

    Tools: project_info, leaderboard_list, record_list, record_show, record_create,
    record_update, record_delete, job_submit, job_list, job_status, and runs.
    Also registers the record
    catalog and record resources, and the propose_implementation prompt.
    """

    @server.tool(
        title="Project information",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def project_info() -> dict[str, Any]:
        """Read the current project settings."""
        return to_jsonable_python(Project.load(selected.path).to_dict(), fallback=dict)

    @server.tool(
        title="List leaderboards",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def leaderboard_list() -> list[str]:
        """List leaderboard names, like `hiraku leaderboard list --json`."""
        return Leaderboard.list(selected)

    @server.tool(
        title="List research records",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        return [item.model_dump(mode="json") for item in selected.vault.list()]

    @server.tool(
        title="Show research record",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_show(record_id: str) -> dict[str, Any]:
        """Read the document's local fields and Markdown."""
        return selected.vault.load(record_id).model_dump(mode="json")

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
        record_id: str,
        title: str | None = None,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        extras = fields or {}
        reserved = {
            "title",
            "id",
            "parent_id",
            "related_to",
            "markdown",
            "record_id",
            "activate",
            "project",
        }
        if reserved.intersection(extras):
            raise ValidationError("Use named arguments for identity and relationships")
        if title is not None:
            extras = {**extras, "title": title}
        record = selected.vault.create(
            record_id,
            parent_id=parent_id,
            markdown=markdown,
            related_to=related_to or [],
            **extras,
        )
        if activate:
            selected.set_active_record(record)
        return record.model_dump(mode="json")

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
        return selected.vault.update(
            selected.vault.load(record_id), fields, markdown=markdown
        ).model_dump(mode="json")

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
        """Delete the document at the given OKF ID."""
        selected.vault.delete(record_id)
        if selected.active_record_id == record_id:
            selected.clear_active_record()
        return {"deleted": record_id}

    @server.tool(
        title="Submit job",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def job_submit(
        command: list[str],
        executor: str = "local-detached",
        record_id: str | None = None,
        executor_url: str | None = None,
        custom_attributes: dict[str, str] | None = None,
        directory: str | None = None,
        launcher: str | None = None,
    ) -> dict[str, Any]:
        """Submit through PSI/J; record attribution requires an explicit record_id."""
        if executor == "local":
            raise ValidationError(
                "Native local execution cannot recover submission status; "
                "use hiraku run or the local-detached executor"
            )
        job = prepare_job(
            command,
            record_id=record_id,
            project=selected,
            directory=Path(directory) if directory is not None else None,
            launcher=launcher,
            attributes=JobAttributes(custom_attributes=custom_attributes)
            if custom_attributes
            else None,
        )
        try:
            JobExecutor.get_instance(executor, url=executor_url).submit(job)
        except (InvalidJobException, SubmitException, ValueError, OSError) as exc:
            raise ExecutionError(str(exc)) from exc
        entry = Submission.from_psi_job(job)
        entry.save(selected)
        return entry.model_dump(mode="json")

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True)
    )
    def job_status(job_id: str) -> dict[str, Any]:
        """Sync and show status by full ID or unique nonempty prefix."""
        entry = Submission.from_prefix(selected, job_id)
        entry.sync()
        entry.save(selected)
        return entry.model_dump(mode="json")

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True)
    )
    def job_list(record_id: str | None = None) -> list[dict[str, Any]]:
        """List jobs and status, refreshing and saving nonfinal submissions."""
        entries = Submission.list(selected, record_id=record_id)
        results = [observe_submission(entry, selected) for entry in entries]
        for result in results:
            if "observation_error" in result:
                logger.warning(
                    "Cannot sync job %s: %s", result["id"], result["observation_error"]
                )
        return results

    @server.tool(
        title="List tracked runs",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def runs(
        leaderboard: str | None = None,
        record_id: str | None = None,
        include_unattributed: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List attributed runs in a leaderboard, optionally filtered by record ID.

        Set include_unattributed to include runs without a record ID.

        Sorting uses plain metric names and their declared direction.
        """
        found = Leaderboard.load(selected, leaderboard).list_runs(
            selected,
            record_id=record_id,
            include_unattributed=include_unattributed,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )
        return to_jsonable_python(found, fallback=dict)

    @server.resource("hiraku://catalog", mime_type="application/json")
    def catalog() -> str:
        """List current OKF record IDs."""
        return json.dumps([record.id for record in selected.vault.list()])

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return _research_content(unquote(record_id), selected)

    @server.prompt(title="Propose implementation")
    def propose_implementation(record_id: str, related_record_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated related records."""
        record = selected.vault.load(record_id)
        related = [
            selected.vault.load(identifier.strip())
            for identifier in related_record_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in related)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Discover other records "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(record.model_dump(mode='json'))}\n\n"
            f"Related records:\n{evidence}"
        )


def register_workflow_tools(server: MCPServer, project: Project) -> None:
    """Register workflow discovery, execution, sessions, and approval.

    Tools: workflow_list, workflow_start, workflow_status, workflow_resume, and
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
    ) -> dict[str, Any]:
        return start_langgraph_workflow(workflow_id, arguments, project=project())

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
    ) -> dict[str, Any]:
        return resume_langgraph_workflow(
            workflow_session_id,
            response,
            project=project(),
            checkpoint_id=checkpoint_id,
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
    return json.dumps(project.vault.load(identifier).model_dump(mode="json"))
