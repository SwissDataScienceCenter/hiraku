"""Stored workflow executions and project-level session lookup."""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from pydantic import ValidationError as PydanticValidationError

from ..config import HirakuConfig
from ..errors import (
    EventRejected,
    NotReady,
    SessionNotFoundError,
    ValidationError,
    WorkflowError,
)
from ..io import atomic_write
from .graph import Event, State
from .workflow import Instruction, Workflow, validation_message

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 30
CANCEL_NOTE = (
    "Cancelling stops the workflow from accepting events. It does not revert "
    "code, delete records, or stop processes that are still running."
)


class EventResult(BaseModel):
    """Submission outcome; instruction_id names the current revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["accepted", "rejected", "not_ready", "stale", "error"]
    instruction_id: int | None = None
    message: str | None = None


class ClientInfo(BaseModel):
    """Optional conversation identifiers supplied by a client hook."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    client: str | None = None
    client_session_id: str | None = None
    client_turn_id: str | None = None


class EventInvocation(BaseModel):
    """One workflow event attempt and its outcome."""

    instruction_id: int
    event: str
    node: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: EventResult
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    client_info: ClientInfo | None = None


class Session(BaseModel):
    """A saved execution. The state owns its node; instructions are snapshots."""

    model_config = ConfigDict(extra="forbid")
    id: str
    workflow: str
    status: Literal["active", "completed", "cancelled"] = "active"
    instruction_id: int = Field(default=0, ge=0)
    state: State
    instruction: Instruction
    history: list[EventInvocation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    _path: Path = PrivateAttr()

    @staticmethod
    def run_start(
        workflow: Workflow,
        arguments: Mapping[str, Any] | None = None,
        *,
        client_info: ClientInfo | None = None,
    ) -> Session:
        """Return the saved session; invalid inputs or execution failures raise."""
        return _run(workflow, workflow.graph.start, arguments, client_info=client_info)

    def run_event(
        self,
        workflow: Workflow | None,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        client_info: ClientInfo | None = None,
    ) -> EventResult:
        """Submit against the latest saved revision; this snapshot is unchanged."""
        if type(instruction_id) is not int or instruction_id < 1:
            raise ValidationError("instruction_id must be a positive integer")
        try:
            with _lock(self._path):
                session = _load(self._path)
                method = workflow.graph.events.get(event) if workflow else None
                status = "rejected"
                if workflow is None:
                    message = "Workflow unavailable; reconnect after fixing it"
                elif session.workflow != workflow.id:
                    message = "Session belongs to another workflow"
                elif session.status == "cancelled":
                    message = "Session is cancelled"
                elif instruction_id != session.instruction_id:
                    status = "stale"
                    message = "Instruction is no longer pending"
                elif session.status != "active":
                    message = f"Session is {session.status}"
                elif method is None or method.source is None:
                    message = f"Unknown event {event!r}"
                elif (
                    event not in session.events
                    or session.state.node not in method.source
                ):
                    message = (
                        f"{event!r} is not an event the current instruction accepts"
                    )
                else:
                    try:
                        updated = _run(
                            workflow,
                            method,
                            arguments,
                            session,
                            client_info=client_info,
                        )
                        return updated.history[-1].result
                    except EventRejected as exc:
                        status = (
                            "not_ready" if isinstance(exc, NotReady) else "rejected"
                        )
                        message = str(exc)
                    except Exception as exc:
                        logger.exception(
                            "Workflow %s event %s failed", workflow.id, event
                        )
                        status = "error"
                        message = (
                            f"{type(exc).__name__}: {exc}. "
                            "No session progress was saved."
                        )
                result = EventResult(
                    status=status,
                    instruction_id=session.instruction_id,
                    message=message,
                )
                return session._record_event(event, instruction_id, result, client_info)
        except Timeout:
            return EventResult(
                status="not_ready",
                message="Session is busy; retry after the other call finishes",
            )

    def validate_workflow(self, workflow: Workflow) -> State:
        """Return a detached concrete state compatible with this workflow.

        Session loading validates the saved snapshot independently of workflow
        code. This method checks ownership, the current state model, node, and
        pending events without executing handlers or changing the session.
        """
        if self.workflow != workflow.id:
            raise WorkflowError("Session belongs to another workflow")
        state = workflow.graph.state_model.model_validate(
            self.state.model_dump(mode="json")
        )
        if state.node not in workflow.graph.nodes:
            raise WorkflowError(f"Unknown node {state.node!r}")
        if not set(self.events).issubset(
            item.name for item in workflow.graph.events_from(state.node)
        ):
            raise WorkflowError("Pending events no longer match the graph")
        return state

    def get_instruction(self, workflow: Workflow) -> dict[str, Any]:
        """Read the saved assignment without executing any workflow handlers."""
        if self.workflow != workflow.id:
            raise ValidationError("Session belongs to another workflow")
        response = self._instruction_response()
        if self.status == "active":
            try:
                self.validate_workflow(workflow)
            except (ValueError, WorkflowError) as exc:
                response.pop("instruction", None)
                response["message"] = f"Incompatible stored state: {exc}"
        return response

    @property
    def events(self) -> list[str]:
        if self.status != "active":
            return []
        return [
            item["event"]
            for item in (self.instruction.events or {}).get("accepted", [])
        ]

    def _save(self) -> None:
        self.updated_at = datetime.now(UTC)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self._path.parent / ".gitignore", "*\n")
        atomic_write(self._path, self.model_dump_json(indent=2) + "\n")

    def _record_event(
        self,
        event: str,
        instruction_id: int,
        result: EventResult,
        client_info: ClientInfo | None,
    ) -> EventResult:
        """Record a failed event under the existing lock; preserve its result."""
        try:
            self.history.append(
                EventInvocation(
                    event=event,
                    node=self.state.node,
                    instruction_id=instruction_id,
                    result=result,
                    client_info=client_info,
                )
            )
            self._save()
        except Exception:
            logger.warning("Could not record workflow event %s", event, exc_info=True)
        return result

    def _instruction_response(self) -> dict[str, Any]:
        response = {
            "session_id": self.id,
            "instruction_id": self.instruction_id,
            "session_status": self.status,
            "node": self.state.node,
        }
        if self.status == "cancelled":
            return {
                **response,
                "message": "Session is cancelled; no actionable instruction.",
            }
        instruction = self.instruction.model_dump(mode="json", exclude_none=True)
        if self.instruction.events is not None:
            instruction["events"] = {
                **instruction["events"],
                "session_id": self.id,
                "instruction_id": self.instruction_id,
            }
        return {**response, "instruction": instruction}

    def show(self, workflow: Workflow | None = None) -> dict[str, Any]:
        """Inspect saved state and history, even when workflow code cannot load."""
        result = self.model_dump(mode="json")
        result.update(self._instruction_response())
        if self.status == "cancelled":
            result.pop("instruction", None)
        elif workflow is None and self.status == "active":
            result.pop("instruction", None)
            result["message"] = "Workflow unavailable; no actionable instruction."
        elif workflow is not None and self.status == "active":
            try:
                self.validate_workflow(workflow)
            except Exception as exc:
                result.pop("instruction", None)
                result["message"] = f"Incompatible workflow: {exc}"
        return result

    def cancel(self) -> dict[str, Any]:
        """Cancel under the session lock without loading workflow code."""
        try:
            with _lock(self._path):
                current = _load(self._path)
                if current.status == "completed":
                    raise ValidationError(f"Session {self.id!r} is already completed")
                current.status = "cancelled"
                current._save()
        except Timeout as exc:
            raise ValidationError("Session is busy; retry after it finishes") from exc
        self.status = "cancelled"
        return {"session_id": self.id, "status": "cancelled", "note": CANCEL_NOTE}


def _run(
    workflow: Workflow,
    method: Event,
    arguments: Mapping[str, Any] | None,
    session: Session | None = None,
    *,
    client_info: ClientInfo | None = None,
) -> Session:
    try:
        validated = method.arguments.model_validate(
            {} if arguments is None else arguments
        )
    except PydanticValidationError as exc:
        raise EventRejected(validation_message(exc)) from exc
    current_state = None
    if session is not None:
        try:
            current_state = session.validate_workflow(workflow)
        except (ValueError, WorkflowError) as exc:
            raise EventRejected(f"Incompatible stored state: {exc}") from exc
    state, instruction = workflow.run(method, validated, current_state)
    snapshot = State.model_validate(state.model_dump(mode="json"))
    if session is None:
        updated = Session(
            id=f"{workflow.id}-{secrets.token_hex(6)}",
            workflow=workflow.id,
            state=snapshot,
            instruction=instruction,
        )
        updated._path = _session_path(updated.id, workflow.config)
    else:
        updated = session.model_copy(deep=True)
    result = EventResult(status="accepted", instruction_id=updated.instruction_id + 1)
    updated.history.append(
        EventInvocation(
            event=method.name,
            node=session.state.node if session is not None else None,
            instruction_id=updated.instruction_id,
            result=result,
            arguments=validated.model_dump(mode="json"),
            client_info=client_info,
        )
    )
    updated.instruction_id += 1
    updated.state = snapshot
    updated.instruction = instruction
    updated.status = "completed" if workflow.graph.nodes[state.node].final else "active"
    updated._save()
    return updated


def _session_path(session_id: str, config: HirakuConfig) -> Path:
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id
    ):
        raise ValidationError(f"Invalid session ID: {session_id!r}")
    return config.sessions_dir / f"{session_id}.json"


def _lock(path: Path) -> FileLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(
        str(path.with_name(f".{path.stem}.lock")), timeout=LOCK_TIMEOUT_SECONDS
    )


def _load(path: Path) -> Session:
    if not path.is_file():
        raise SessionNotFoundError(f"Session {path.stem!r} not found")
    try:
        session = Session.model_validate_json(path.read_text(encoding="utf-8"))
        if not session.history or session.instruction_id < 1:
            raise ValidationError("Session must have history and a positive revision")
        if session.id != path.stem:
            raise ValidationError("Session ID does not match its filename")
    except (ValueError, OSError) as exc:
        raise ValidationError(f"Invalid session {path.stem!r}: {exc}") from exc
    session._path = path
    return session


def load_session(session_id: str, *, config: HirakuConfig | None = None) -> Session:
    """Load a session by ID from the selected or discovered project."""
    selected = config if config is not None else HirakuConfig.discover()
    return _load(_session_path(session_id, selected))


def list_sessions(*, config: HirakuConfig | None = None) -> list[Session]:
    """List sessions newest first; log and skip malformed or unsupported files."""
    selected = config if config is not None else HirakuConfig.discover()
    sessions = []
    for path in sorted(selected.sessions_dir.glob("*.json")):
        try:
            sessions.append(_load(path))
        except (ValidationError, SessionNotFoundError) as exc:
            logger.warning("Session %s is unavailable: %s", path.stem, exc)
    return sorted(sessions, key=lambda item: item.updated_at, reverse=True)
