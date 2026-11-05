"""Hiraku MCP workflow tools, and compact interrupt adapter."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from mcp.server.mcpserver import Elicit, ElicitationResult, MCPServer, Resolve
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import ValidationError
from ..project import Project
from ..workflows.langgraph import (
    resume_workflow as resume_langgraph_workflow,
)
from ..workflows.langgraph import (
    start_workflow as start_langgraph_workflow,
)
from ..workflows.langgraph import (
    workflow_details as langgraph_workflow_details,
)
from ..workflows.langgraph import (
    workflow_status as langgraph_workflow_status,
)
from ..workflows.session_history import list_sessions, session_history


class HumanDecision(BaseModel):
    """Form data supplied by MCP elicitation; revision is server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "revise", "stop"] = Field(
        description="Approve, request changes, or stop."
    )
    feedback: str | None = Field(
        default=None, description="Required instructions when requesting changes."
    )

    @model_validator(mode="after")
    def validate_feedback(self) -> HumanDecision:
        if self.decision == "revise" and not (self.feedback or "").strip():
            raise ValueError("feedback is required when decision is 'revise'")
        if self.decision != "revise" and self.feedback is not None:
            raise ValueError("feedback is only allowed when decision is 'revise'")
        return self


class WorkflowNextAction(TypedDict):
    actor: Literal["agent", "human"]
    interaction: Literal["task", "review", "approval"]
    action: str
    requires_approval: bool


class WorkflowResult(TypedDict):
    workflow_session_id: str
    workflow_id: str
    status: Literal["waiting", "completed"]
    interrupts: list[dict[str, Any]]
    next_action: WorkflowNextAction | None
    next_actions: list[WorkflowNextAction]
    checkpoint_id: str | None
    result: NotRequired[Any]
    decision: NotRequired[Literal["decline", "cancel"]]


def _get_project(config: Project | None) -> Project:
    return config or Project.discover()


def _pending_action(status: dict[str, Any], checkpoint_id: str) -> dict[str, Any]:
    """Validate the persisted checkpoint and return its sole interrupt."""
    if status.get("status") != "waiting":
        raise ValidationError("Workflow is not waiting for a response")
    if status.get("checkpoint_id") != checkpoint_id:
        raise ValidationError(
            "Workflow checkpoint has changed; inspect workflow_status"
        )
    interrupts = status.get("interrupts")
    if not isinstance(interrupts, list) or len(interrupts) != 1:
        raise ValidationError("Exactly one pending interrupt is required")
    pending = interrupts[0].get("value")
    if not isinstance(pending, dict):
        raise ValidationError("Pending interrupt must be an object")
    actor = pending.get("actor")
    interaction = pending.get("interaction", "task" if actor == "agent" else None)
    if actor == "agent" and interaction in (None, "task"):
        return pending
    if actor == "human" and interaction in ("approval", "review"):
        return pending
    raise ValidationError("Unsupported workflow interrupt format")


def normalized_next_action(value: dict[str, Any]) -> WorkflowNextAction:
    """Reusable by the LangGraph snapshot serializer for compact status fields."""
    actor = value.get("actor")
    interaction = value.get("interaction", "task" if actor == "agent" else None)
    if actor not in ("agent", "human") or interaction not in (
        "task",
        "review",
        "approval",
    ):
        raise ValidationError("Unsupported workflow interrupt format")
    if (actor, interaction) not in (
        ("agent", "task"),
        ("human", "review"),
        ("human", "approval"),
    ):
        raise ValidationError("Inconsistent workflow interrupt actor and interaction")
    action = value.get("action")
    if not isinstance(action, str) or not action:
        raise ValidationError("Workflow action is missing")
    return {
        "actor": actor,
        "interaction": interaction,
        "action": action,
        "requires_approval": interaction == "approval",
    }


def _human_message(pending: dict[str, Any]) -> str:
    """Show the proposal only; never dump the entire workflow checkpoint."""
    message = pending.get("message") or "Review the proposed workflow action."
    proposal = pending.get("proposal", {})
    # Keep the prompt useful but bounded. The full proposal remains in status.
    rendered = json.dumps(proposal, indent=2, ensure_ascii=False, default=str)
    if len(rendered) > 12000:
        rendered = (
            rendered[:12000]
            + "\n[Proposal truncated; inspect workflow_status before deciding]"
        )
    return f"{message}\n\nProposal:\n{rendered}\n\nChoose approve, revise, or stop."


def register_workflow_tools(server: MCPServer, config: Project | None) -> None:
    """Register the existing six public workflow operations (no API renames)."""

    def get_status(workflow_session_id: str) -> dict[str, Any]:
        return langgraph_workflow_status(
            workflow_session_id, project=_get_project(config)
        )

    def resolve_resume_response(
        workflow_session_id: str,
        response: dict[str, Any],
        checkpoint_id: str,
    ) -> Elicit[HumanDecision] | dict[str, Any]:
        pending = _pending_action(get_status(workflow_session_id), checkpoint_id)
        interaction = pending.get("interaction", "task")
        if pending["actor"] == "agent":
            # The workflow runner validates these before checkpoint resumption.
            if not isinstance(response, dict):
                raise ValidationError("Workflow response must be an object")
            return response
        if interaction == "review":
            if not isinstance(response, dict) or not response:
                raise ValidationError(
                    "Human review requires a non-empty review response"
                )
            schema = pending.get("response_schema")
            if isinstance(schema, dict):
                try:
                    validate_json_schema(response, schema)
                except JsonSchemaValidationError as exc:
                    raise ValidationError(
                        "Workflow response does not match response_schema: "
                        f"{exc.message}"
                    ) from exc
            return response
        # Only a protected human approval requests structured elicitation.
        return Elicit(message=_human_message(pending), schema=HumanDecision)

    def workflow_list() -> list[dict[str, Any]]:
        return langgraph_workflow_details(project=_get_project(config))

    def workflow_start(
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

    def workflow_sessions() -> list[dict[str, Any]]:
        return list_sessions(project=_get_project(config))

    def workflow_session_history(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> dict[str, Any]:
        return session_history(workflow_session_id, project=_get_project(config))

    def workflow_status(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
    ) -> WorkflowResult:
        return cast(WorkflowResult, get_status(workflow_session_id))

    def workflow_resume(
        workflow_session_id: Annotated[
            str, Field(description="Workflow session ID returned by workflow_start.")
        ],
        response: Annotated[
            dict[str, Any],
            Field(description="Agent task result, or empty object for a human gate."),
        ],
        checkpoint_id: Annotated[
            str, Field(description="Checkpoint ID from workflow_status.")
        ],
        approval: Annotated[
            ElicitationResult[HumanDecision], Resolve(resolve_resume_response)
        ],
        context: Annotated[
            dict[str, Any] | None, Field(description="Optional transition context.")
        ] = None,
    ) -> WorkflowResult:
        if isinstance(approval, dict):
            # Compatibility with resolver implementations returning raw values.
            pending = _pending_action(get_status(workflow_session_id), checkpoint_id)
            interaction = pending.get("interaction", "task")
            if pending["actor"] != "agent" and interaction != "review":
                raise ValidationError(
                    "Human approval must be obtained through MCP elicitation"
                )
            resume_response = approval
        elif approval.action != "accept":
            # Decline/cancel does not consume the checkpoint.
            result = get_status(workflow_session_id)
            result["decision"] = approval.action
            return cast(WorkflowResult, result)
        elif isinstance(approval.data, dict):
            # MCP SDK v2 wraps a plain resolver value in AcceptedElicitation too.
            # Re-read the checkpoint before accepting it so a dict cannot stand
            # in for the required human approval decision.
            pending = _pending_action(get_status(workflow_session_id), checkpoint_id)
            interaction = pending.get("interaction", "task")
            if pending["actor"] != "agent" and interaction != "review":
                raise ValidationError(
                    "Human approval must be obtained through MCP elicitation"
                )
            resume_response = approval.data
        elif isinstance(approval.data, HumanDecision):
            # Revalidate after the elicitation round trip; the resume service must
            # also serialize/atomically consume the checkpoint to prevent races.
            pending = _pending_action(get_status(workflow_session_id), checkpoint_id)
            if (
                pending["actor"] != "human"
                or pending.get("interaction", "approval") != "approval"
            ):
                raise ValidationError(
                    "Pending human approval changed during elicitation"
                )
            resume_response = approval.data.model_dump(exclude_none=True)
            if pending.get("interaction") == "approval":
                revision = pending.get("revision")
                if not isinstance(revision, str) or not revision:
                    raise ValidationError(
                        "Protected approval is missing proposal revision"
                    )
                # Never accept a revision identifier from agent/tool arguments.
                resume_response["revision"] = revision
        else:
            raise ValidationError("Elicitation returned invalid user data")
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

    # Preserve annotation resolution across postponed annotations in SDK v2.
    workflow_resume.__annotations__["approval"] = Annotated[
        ElicitationResult[HumanDecision], Resolve(resolve_resume_response)
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
        description="Start a workflow; return its next agent task or human decision.",
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
        description="List recorded workflow sessions.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_session_history,
        title="Read workflow session history",
        description="Read session history and metadata.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_status,
        title="Check workflow status",
        description="Inspect the checkpoint without advancing it.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    server.add_tool(
        workflow_resume,
        title="Resume workflow",
        description=(
            "Resume one paused step. Agent tasks accept direct responses; "
            "human reviews and protected approvals request MCP elicitation. "
            "Inspect the next action after each resume."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
