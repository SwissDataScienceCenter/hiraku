"""Stored workflow executions and project-level session lookup."""

from __future__ import annotations

import json
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
from .graph import Event, EventArguments, State
from .workflow import Instruction, Workflow, validation_message

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 30
CANCEL_NOTE = (
    "Cancelling stops the workflow from accepting events. It does not revert "
    "code, delete records, or stop processes that are still running."
)


class EventResult(BaseModel):
    """Submission outcome without an assignment; IDs name the current revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["accepted", "rejected", "not_ready", "stale", "error"]
    session_id: str | None = None
    instruction_id: int | None = None
    message: str | None = None


class HistoryEntry(BaseModel):
    """One accepted submission, addressing the preceding instruction."""

    instruction_id: int
    event: str
    arguments: dict[str, Any]
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Session(BaseModel):
    """A saved execution. The state owns its node; instructions are snapshots."""

    model_config = ConfigDict(extra="forbid")
    format_version: Literal[2] = 2
    id: str
    workflow: str
    status: Literal["active", "completed", "cancelled"] = "active"
    instruction_id: int = Field(default=0, ge=0)
    state: State
    instruction: Instruction
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    _path: Path = PrivateAttr()

    @staticmethod
    def run_start(
        workflow: Workflow, arguments: Mapping[str, Any] | None = None
    ) -> EventResult:
        """Run the entry event and store its instruction; return an outcome."""
        try:
            validated = workflow.graph.start.arguments.model_validate(
                {} if arguments is None else arguments
            )
        except PydanticValidationError as exc:
            return EventResult(status="rejected", message=validation_message(exc))
        return _run(workflow, workflow.graph.start, validated)

    def run_event(
        self,
        workflow: Workflow,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> EventResult:
        """Submit against the latest saved revision; this snapshot is unchanged."""
        if type(instruction_id) is not int or instruction_id < 1:
            raise ValidationError("instruction_id must be a positive integer")
        try:
            with _lock(self._path):
                session = _load(self._path)
                if session.workflow != workflow.id:
                    return session._result(
                        "rejected", "Session belongs to another workflow"
                    )
                if session.status == "cancelled":
                    return session._result("rejected", "Session is cancelled")
                method = workflow.graph.events.get(event)
                if method is None or method.source is None:
                    return session._result("rejected", f"Unknown event {event!r}")
                try:
                    validated = method.arguments.model_validate(
                        {} if arguments is None else arguments
                    )
                    values = validated.model_dump(mode="json")
                except PydanticValidationError as exc:
                    return session._result("rejected", validation_message(exc))
                last = session.history[-1]
                if last.instruction_id == instruction_id:
                    if last.event == event and last.arguments == values:
                        return session._result("accepted")
                    return session._result("stale", "Instruction was already answered")
                if session.status != "active":
                    return session._result("rejected", f"Session is {session.status}")
                if instruction_id != session.instruction_id:
                    return session._result("stale", "Instruction is no longer pending")
                if (
                    event not in session.events
                    or session.state.node not in method.source
                ):
                    return session._result(
                        "rejected",
                        f"{event!r} is not an event the current instruction accepts",
                    )
                try:
                    current_state = session.validate_workflow(workflow)
                except (ValueError, WorkflowError) as exc:
                    return session._result(
                        "rejected", f"Incompatible stored state: {exc}"
                    )
                return _run(
                    workflow, method, validated, session, current_state=current_state
                )
        except Timeout:
            return EventResult(
                status="not_ready",
                session_id=self.id,
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

    def _result(self, status: str, message: str | None = None) -> EventResult:
        return EventResult(
            status=status,
            session_id=self.id,
            instruction_id=self.instruction_id,
            message=message,
        )

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
            result["message"] = "Workflow unavailable; instruction is historical."
        elif workflow is not None and self.status == "active":
            try:
                self.validate_workflow(workflow)
            except Exception as exc:
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
    arguments: EventArguments,
    session: Session | None = None,
    *,
    current_state: State | None = None,
) -> EventResult:
    previous = session
    if session is not None:
        session = session.model_copy(deep=True)
    try:
        values = arguments.model_dump(mode="json")
        state, instruction = workflow.run(method, arguments, current_state)
        snapshot = State.model_validate(state.model_dump(mode="json"))
        if session is None:
            session = Session(
                id=f"{workflow.id}-{secrets.token_hex(6)}",
                workflow=workflow.id,
                state=snapshot,
                instruction=instruction,
            )
            session._path = _session_path(session.id, workflow.config)
        session.history.append(
            HistoryEntry(
                instruction_id=session.instruction_id,
                event=method.name,
                arguments=values,
            )
        )
        session.instruction_id += 1
        session.state = snapshot
        session.instruction = instruction
        session.status = (
            "completed" if workflow.graph.nodes[state.node].final else "active"
        )
        session._save()
        return session._result("accepted")
    except (EventRejected, NotReady) as exc:
        status = "not_ready" if isinstance(exc, NotReady) else "rejected"
        if session is not None:
            return session._result(status, str(exc))
        return EventResult(status=status, message=str(exc))
    except Exception as exc:
        logger.exception("Workflow %s event %s failed", workflow.id, method.name)
        message = f"{type(exc).__name__}: {exc}. No session progress was saved."
        if previous is not None:
            return previous._result("error", message)
        return EventResult(status="error", message=message)


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
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format_version") != 2:
            raise ValidationError("Unsupported session format; start a new session")
        session = Session.model_validate(raw)
        if not session.history or session.instruction_id != len(session.history):
            raise ValidationError("Session revision does not match its history")
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
