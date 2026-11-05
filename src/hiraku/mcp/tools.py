"""MCP tools, resources, and prompts for a Hiraku project."""

from __future__ import annotations

import inspect
import json
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast
from urllib.parse import quote, unquote

from anyio.from_thread import check_cancelled
from mcp import MCPError
from mcp.server.mcpserver import Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    MissingRequiredClientCapabilityErrorData,
    ToolAnnotations,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import to_jsonable_python

from ..errors import ValidationError
from ..execution import execute_command as execute_project_command
from ..project import Project
from ..record import Record, list_records, load_record
from ..workflows.langgraph import resume_workflow as resume_langgraph_workflow
from ..workflows.langgraph import start_workflow as start_langgraph_workflow
from ..workflows.langgraph import workflow_details as langgraph_workflow_details
from ..workflows.langgraph import workflow_ids, workflow_mermaid
from ..workflows.langgraph import workflow_status as langgraph_workflow_status
from ..workflows.session_history import list_sessions, session_history


class WorkflowApproval(BaseModel):
    model_config = ConfigDict(
        json_schema_extra=lambda schema: schema.pop("title", None)
    )

    decision: Literal["approve", "revise", "stop"] = Field(
        description="Whether to approve, request a revision, or stop."
    )


class WorkflowInterrupt(TypedDict):
    id: str
    value: Any


class WorkflowNextAction(TypedDict):
    actor: Literal["agent", "human"]
    interaction: Literal["task", "review", "approval"]
    action: str
    requires_approval: bool


class WorkflowResult(TypedDict):
    workflow_session_id: str
    workflow_id: str
    status: Literal["waiting", "completed"]
    interrupts: list[WorkflowInterrupt]
    next_action: WorkflowNextAction | None
    next_actions: list[WorkflowNextAction]
    checkpoint_id: str | None
    result: NotRequired[Any]
    decision: NotRequired[Literal["decline", "cancel"]]


class WorkflowSession(TypedDict):
    workflow_session_id: str
    workflow_id: str
    status: str
    created_at: str
    updated_at: str
    context: dict[str, Any]
    checkpoint_id: str | None


class WorkflowSessionEvent(TypedDict):
    id: int
    event: str
    timestamp: str
    context: dict[str, Any]
    checkpoint_id: str | None
    error_type: str | None


class WorkflowHistory(TypedDict):
    session: WorkflowSession
    events: list[WorkflowSessionEvent]


class WorkflowDetail(TypedDict):
    id: str
    description: str | None
    required_capabilities: NotRequired[list[str]]
    input_schema: dict[str, Any]


def _data(record: Record) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def _update(record: Record, fields: dict[str, Any], markdown: str | None) -> Record:
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
    updated.save(record.path, overwrite=True)
    return updated


def _get_project(config: Project | None) -> Project:
    return config or Project.discover()


def register_project_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register project-level tools.

    Tools:
    - `project_info`: Read the current project configuration and metadata.
    """

    @server.tool(
        title="Project information",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def project_info() -> dict[str, Any]:
        """Read the current project configuration and metadata."""
        return to_jsonable_python(_get_project(config).to_dict(), fallback=dict)


def register_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register all Hiraku MCP capabilities on a server.

    Features include:
    - Project-level tools and resources
    - Record management tools and resources
    - Workflow management tools and resources
    """
    register_project_tools(server, config)
    register_record_tools(server, config)
    register_record_resources(server, config)
    register_record_prompts(server, config)
    register_workflow_tools(server, config)
    register_workflow_prompts(server, config)
    register_workflow_resources(server, config)


def register_record_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register record, execution, and tracked-run tools.

    Tools:
    - `record_list`: List research records in the selected project.
    - `record_show`: Read a research record and its effective inherited fields.
    - `record_create`: Create a research record, optionally activating it.
    - `record_update`: Merge record fields and optionally replace Markdown.
    - `record_reparent`: Move a record subtree under a parent, or make it a root.
    - `record_delete`: Delete a record using Hiraku's relationship checks.
    - `runs`: List tracked runs attributed to a record, like `hiraku runs`.
    - `execute_command`: Run a command and return output with exit code.
    """

    @server.tool(
        title="List research records",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        project = _get_project(config)
        return [_data(item) for item in list_records(project=project)]

    @server.tool(
        title="Show research record",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record and its effective inherited fields."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        resolved = record.resolve(project=project)
        return {
            **_data(record),
            "resolved": resolved.values,
            "sources": resolved.sources,
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
        record_id: str,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        project = _get_project(config)
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
                project=project,
                **extras,
            )
        )

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
        project = _get_project(config)
        return _data(_update(load_record(record_id, project=project), fields, markdown))

    @server.tool(
        title="Reparent research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def record_reparent(record_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Move a record subtree under a parent, or make it a root."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        record.reparent(parent_id, project=project)
        return _data(record)

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
        project = _get_project(config)
        load_record(record_id, project=project).delete(project=project)
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
        """Run a command and return output with exit code."""
        project = _get_project(config)
        result = execute_project_command(
            command,
            record_id=record_id,
            dispatch=dispatch,
            capture_output=True,
            project=project,
            cancel_check=check_cancelled,
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
        """List tracked runs attributed to a record, like `hiraku runs`."""
        if ascending is not None and sort is None:
            raise ValidationError("A sort direction requires sort")
        project = _get_project(config)
        record = load_record(record_id, project=project)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = record.resolve(project=project).values.get("metrics") or []
            directions = {item.name: item.direction for item in declarations}
            query["sort"] = sort
            query["ascending"] = (
                ascending
                if ascending is not None
                else directions.get(sort) != "maximize"
            )
        runs = record.resolve_tracking_backend(project=project).list_runs(
            record_id=None if all_records else record.id,
            **query,
        )
        return to_jsonable_python(runs, fallback=dict)


def register_record_resources(
    server: MCPServer,
    config: Project | None,
) -> None:
    """Register actual attached files, excluding Markdown records."""

    @server.resource(
        "hiraku://catalog",
        mime_type="application/json",
    )
    def catalog() -> str:
        project = _get_project(config)
        records = list_records(project=project)

        # Identify all record Markdown files.
        record_paths = {record.path.resolve() for record in records}

        uris: list[str] = []

        for record in records:
            root = record.path.parent.resolve()

            for path in sorted(root.rglob("*")):
                resolved = path.resolve()

                if not resolved.is_file():
                    continue

                if not resolved.is_relative_to(root):
                    continue

                # Do not expose tasks or experiments as attachments.
                if resolved in record_paths:
                    continue

                relative = path.relative_to(root).as_posix()

                uris.append(f"hiraku://sources/{record.id}/{quote(relative, safe='')}")

        return json.dumps(sorted(set(uris)))

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        """Read a live record and its current metadata as JSON."""
        project = _get_project(config)
        return json.dumps(_data(load_record(record_id, project=project)))

    @server.resource("hiraku://sources/{record_id}/{source_path}")
    def source_resource(
        record_id: str,
        source_path: str,
    ) -> bytes:
        project = _get_project(config)
        record = load_record(record_id, project=project)

        root = record.path.parent.resolve()
        path = (root / unquote(source_path)).resolve()

        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be inside the record directory")

        # Also enforce the exclusion when reading directly.
        record_paths = {item.path.resolve() for item in list_records(project=project)}

        if path in record_paths:
            raise ResourceError("Record Markdown files are not attached sources")

        return path.read_bytes()


def register_record_prompts(server: MCPServer, config: Project | None) -> None:
    """Register prompts grounded in records and related evidence."""

    @server.prompt(title="Propose implementation")
    def propose_implementation(
        record_id: Annotated[
            str, Field(description="Research record ID to ground the proposal in.")
        ],
        related_record_ids: Annotated[
            str,
            Field(description="Optional comma-separated related record IDs."),
        ] = "",
    ) -> str:
        """Ground a proposal in a record and comma-separated related records."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        related = [
            load_record(identifier.strip(), project=project)
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


def register_workflow_tools(server: MCPServer, config: Project | None) -> None:
    """Register workflow lifecycle and session history tools."""

    async def resolve_resume_response(
        workflow_session_id: str,
        response: dict[str, Any],
        checkpoint_id: str,
        context: Context,
    ) -> Elicit[WorkflowApproval] | dict[str, Any]:
        status = langgraph_workflow_status(
            workflow_session_id, project=_get_project(config)
        )
        if status["checkpoint_id"] != checkpoint_id:
            raise ValidationError("Workflow checkpoint has changed; check its status")
        interrupts = status["interrupts"]
        if status["status"] != "waiting":
            raise ValidationError("Workflow is not waiting for a response")
        if len(interrupts) != 1:
            raise ValidationError(
                "workflow_resume requires exactly one pending interrupt; "
                "this session has multiple interrupts and cannot be resumed safely"
            )
        pending = interrupts[0].get("value")
        if not isinstance(pending, dict):
            return response
        interaction = pending.get("interaction")
        if isinstance(interaction, dict):
            interaction_type = interaction.get("type")
            mode = interaction.get("mode")
        else:
            interaction_type = interaction if isinstance(interaction, str) else None
            mode = pending.get("interaction_mode")
        actor = pending.get("actor")
        if actor not in {"agent", "human"}:
            # Older workflow definitions used interaction.mode and approval.
            actor = "human" if mode == "approval" or "approval" in pending else "agent"
        if actor == "agent":
            if not isinstance(response, dict):
                raise ValidationError("Agent workflow response must be an object")
            return response
        if actor != "human":
            raise ValidationError(f"Unknown workflow interrupt actor: {actor!r}")
        if interaction_type not in {"review", "approval", None}:
            raise ValidationError(
                f"Human workflow interaction must be review or approval, got "
                f"{interaction_type!r}"
            )
        interrupt_value = pending
        approval = interrupt_value.get("approval", {})
        if not isinstance(approval, dict):
            raise ValidationError("Human workflow interrupt has invalid approval data")
        capabilities = context.client_capabilities
        elicitation = capabilities.elicitation if capabilities is not None else None
        if elicitation is None or elicitation.form is None:
            required_capabilities = ClientCapabilities(
                elicitation=ElicitationCapability(form=FormElicitationCapability())
            )
            error_data = MissingRequiredClientCapabilityErrorData(
                required_capabilities=required_capabilities
            )
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=(
                    "This workflow requires approval, but this MCP client does "
                    "not support form elicitation. Reconnect with an "
                    "elicitation-capable client, then call workflow_resume again. "
                    "The workflow is still paused."
                ),
                data=error_data.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                ),
            )
        proposal_context = json.dumps(interrupt_value, sort_keys=True, indent=2)
        return Elicit(
            message=(
                f"{approval.get('message', 'Review the proposed workflow action.')}"
                f"\n\nPending interrupt and stored proposal:\n"
                f"{proposal_context}"
                "\n\nChoose approve, revise, or stop."
            ),
            schema=WorkflowApproval,
        )

    def workflow_list() -> list[WorkflowDetail]:
        return cast(
            list[WorkflowDetail],
            langgraph_workflow_details(project=_get_project(config)),
        )

    async def workflow_start(
        workflow_id: Annotated[str, Field(description="Project workflow ID.")],
        arguments: Annotated[
            dict[str, Any] | None, Field(description="Workflow input object.")
        ] = None,
        context: Annotated[
            dict[str, Any] | None,
            Field(description="Optional caller-provided session context."),
        ] = None,
    ) -> WorkflowResult:
        return cast(
            WorkflowResult,
            start_langgraph_workflow(
                workflow_id, arguments, project=_get_project(config), context=context
            ),
        )

    async def workflow_sessions() -> list[WorkflowSession]:
        return cast(
            list[WorkflowSession],
            list_sessions(project=_get_project(config)),
        )

    async def workflow_session_history(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> WorkflowHistory:
        return cast(
            WorkflowHistory,
            session_history(workflow_session_id, project=_get_project(config)),
        )

    async def workflow_status(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> WorkflowResult:
        return cast(
            WorkflowResult,
            langgraph_workflow_status(
                workflow_session_id, project=_get_project(config)
            ),
        )

    async def workflow_resume(
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
        approval: Annotated[
            ElicitationResult[WorkflowApproval], Resolve(resolve_resume_response)
        ],
        context: Annotated[
            dict[str, Any] | None,
            Field(description="Optional caller-provided transition context."),
        ] = None,
    ) -> WorkflowResult:
        if isinstance(approval, dict):
            resume_response = approval
        elif approval.action != "accept":
            result = langgraph_workflow_status(
                workflow_session_id, project=_get_project(config)
            )
            result["decision"] = approval.action
            return cast(WorkflowResult, result)
        elif isinstance(approval.data, WorkflowApproval):
            resume_response: dict[str, Any] = {
                "approval": {
                    "decision": approval.data.decision,
                }
            }
            pending_status = langgraph_workflow_status(
                workflow_session_id, project=_get_project(config)
            )
            if pending_status["checkpoint_id"] != checkpoint_id:
                raise ValidationError(
                    "Workflow checkpoint changed during elicitation; inspect status"
                )
            pending = pending_status["interrupts"]
            if len(pending) != 1 or not isinstance(pending[0].get("value"), dict):
                raise ValidationError(
                    "Approval requires exactly one current workflow interrupt"
                )
            pending_approval = pending[0]["value"].get("approval", {})
            revision = pending_approval.get("revision")
            if revision is not None:
                resume_response["approval"]["revision"] = revision
            elif pending[0]["value"].get("actor") != "human":
                # Pre-ownership workflow interrupts expected the proposal result
                # to be echoed in ordinary arguments.
                resume_response["response"] = response
        else:
            resume_response = approval.data
        return cast(
            WorkflowResult,
            resume_langgraph_workflow(
                workflow_session_id,
                resume_response,
                project=_get_project(config),
                checkpoint_id=checkpoint_id,
                context=context,
            ),
        )

    workflow_resume.__annotations__["approval"] = Annotated[
        ElicitationResult[WorkflowApproval], Resolve(resolve_resume_response)
    ]

    server.add_tool(
        workflow_list,
        title="List workflows",
        description="List project LangGraph workflows.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_start,
        title="Start workflow",
        description=(
            "Start a LangGraph workflow and return its interrupt or final result."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    server.add_tool(
        workflow_sessions,
        title="List workflow sessions",
        description="List recorded workflow sessions and their metadata.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_status,
        title="Check workflow status",
        description=(
            "Inspect a checkpointed LangGraph workflow run without advancing it."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_resume,
        title="Resume workflow",
        description=(
            "Resume a paused LangGraph run. Review and automatic interrupts "
            "accept their structured response directly; approval-mode interrupts "
            "elicit the user's approve, revise, or stop decision during this "
            "call. The run may return another waiting interrupt; inspect it and "
            "call this tool again for the next phase."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )


def register_workflow_prompts(server: MCPServer, config: Project | None) -> None:
    """Register a typed MCP prompt for each workflow without starting it."""
    project = _get_project(config)
    for workflow in langgraph_workflow_details(project=project):
        workflow_id = workflow["id"]
        schema = workflow["input_schema"]
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        parameters = []

        for name, field in properties.items():
            description = field.get("description", "")
            field_type = field.get("type")
            if isinstance(field_type, list):
                field_type = next(
                    (item for item in field_type if item != "null"), "string"
                )
            representation = (
                "JSON text" if field_type in {"object", "array"} else field_type
            )
            if representation:
                description = f"Provide {representation}. {description}".strip()
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=(inspect.Parameter.empty if name in required else None),
                    annotation=Annotated[
                        str if name in required else str | None,
                        Field(description=description or None),
                    ],
                )
            )

        def make_prompt(
            selected_id: str,
            input_schema: dict[str, Any],
            prompt_parameters: list[inspect.Parameter],
        ) -> Any:
            def prompt(**values: str | None) -> str:
                supplied: dict[str, Any] = {}
                for key, value in values.items():
                    if value is None:
                        continue
                    field = input_schema.get("properties", {}).get(key, {})
                    field_type = field.get("type")
                    if isinstance(field_type, list):
                        field_type = next(
                            (item for item in field_type if item != "null"), "string"
                        )
                    if field_type in {
                        "object",
                        "array",
                        "integer",
                        "number",
                        "boolean",
                    }:
                        try:
                            supplied[key] = json.loads(value)
                        except json.JSONDecodeError as exc:
                            raise ValidationError(
                                f"Workflow argument {key!r} must be valid "
                                f"{field_type} JSON text"
                            ) from exc
                    else:
                        supplied[key] = value

                return (
                    "Call the workflow_start tool with:\n"
                    f"workflow_id: {json.dumps(selected_id)}\n"
                    "arguments:\n"
                    f"{json.dumps(supplied, ensure_ascii=False, indent=2)}\n\n"
                    "This prompt prepares the call; it does not start the workflow."
                )

            prompt.__name__ = selected_id.replace("-", "_")
            prompt.__signature__ = inspect.Signature(prompt_parameters)
            prompt.__annotations__ = {
                parameter.name: parameter.annotation for parameter in prompt_parameters
            }
            return prompt

        server.add_prompt(
            Prompt.from_function(
                make_prompt(workflow_id, schema, parameters),
                name=workflow_id,
                title=f"{workflow_id.replace('-', ' ').title()} workflow",
                description=(
                    f"Prepare typed inputs for the {workflow_id} workflow and "
                    "return a suggested workflow_start call."
                ),
            )
        )


def register_workflow_resources(server: MCPServer, config: Project | None) -> None:
    """Register workflow definition and graph resources."""

    @server.resource(
        "hiraku://workflows/{workflow_id}",
        name="workflow",
        description="Markdown definition for a project LangGraph workflow",
        mime_type="text/markdown",
    )
    def workflow_resource(workflow_id: str) -> str:
        """Read the Markdown definition for a project LangGraph workflow."""
        current = _get_project(config)
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
        return workflow_mermaid(workflow_id, project=_get_project(config))
