"""Stored workflow executions and project-level session lookup."""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_json

from ..config import HirakuConfig
from ..errors import (
    EventRejected,
    NotReady,
    SessionNotFoundError,
    ValidationError,
    WorkflowError,
)
from ..io import atomic_write
from .graph import State
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
    """An execution, initially unstarted; saved instructions are snapshots."""

    model_config = ConfigDict(extra="forbid")
    id: str
    workflow: str
    status: Literal["active", "completed", "cancelled"] = "active"
    instruction_id: int = Field(default=0, ge=0)
    state: State | None = None
    instruction: Instruction | None = None
    history: list[EventInvocation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    _path: Path = PrivateAttr()
    _write_lock: FileLock | None = PrivateAttr(default=None)

    @classmethod
    def create(cls, workflow: Workflow) -> Session:
        """Allocate an unstarted session in memory, without executing or saving."""
        session = cls(id=f"{workflow.id}-{secrets.token_hex(6)}", workflow=workflow.id)
        session._path = _session_path(session.id, workflow.config)
        return session

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Refresh this instance under its lock and enable writes until exit."""
        if self._write_lock is not None and self._write_lock.is_locked:
            raise ValidationError("Session is already locked by this caller")
        try:
            lock = _lock(self._path)
            lock.acquire()
        except Timeout as exc:
            raise ValidationError("Session is busy; retry after it finishes") from exc
        try:
            if self.instruction_id != 0 or self._path.is_file():
                current = _load(self._path)
                for name in type(self).model_fields:
                    setattr(self, name, getattr(current, name))
            self._write_lock = lock
            yield
        finally:
            self._write_lock = None
            lock.release()

    def run_start(
        self,
        workflow: Workflow,
        arguments: Mapping[str, Any] | None = None,
        *,
        client_info: ClientInfo | None = None,
    ) -> EventResult:
        """Execute the start event; initialize this session only after saving."""
        return self._run(
            workflow, 0, workflow.graph.start.name, arguments, client_info=client_info
        )

    def run_event(
        self,
        workflow: Workflow,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        client_info: ClientInfo | None = None,
    ) -> EventResult:
        """Submit an event while holding the session lock."""
        if type(instruction_id) is not int or instruction_id < 1:
            raise ValidationError("instruction_id must be a positive integer")
        return self._run(
            workflow, instruction_id, event, arguments, client_info=client_info
        )

    def _run(
        self,
        workflow: Workflow,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None,
        *,
        client_info: ClientInfo | None,
    ) -> EventResult:
        if self._write_lock is None or not self._write_lock.is_locked:
            raise ValidationError("Session writes require 'with session.lock()'")
        method = workflow.graph.events.get(event)
        status = "rejected"
        if self.workflow != workflow.id:
            message = "Session belongs to another workflow"
        elif self.status == "cancelled":
            message = "Session is cancelled"
        elif self.instruction_id == 0 and method is not workflow.graph.start:
            message = "Session has not started"
        elif instruction_id != self.instruction_id:
            status = "stale"
            message = "Instruction is no longer pending"
        elif self.status != "active":
            message = f"Session is {self.status}"
        elif method is None or (method.source is None and self.instruction_id != 0):
            message = f"Unknown event {event!r}"
        elif method.source is not None and (
            event not in self.events
            or self.state is None
            or self.state.node not in method.source
        ):
            message = f"{event!r} is not an event the current instruction accepts"
        else:
            try:
                try:
                    validated = method.arguments.model_validate(
                        {} if arguments is None else arguments
                    )
                except PydanticValidationError as exc:
                    raise EventRejected(validation_message(exc)) from exc
                try:
                    current_state = (
                        self.validate_workflow(workflow)
                        if self.state is not None
                        else None
                    )
                except (ValueError, WorkflowError) as exc:
                    raise EventRejected(f"Incompatible stored state: {exc}") from exc
                state, instruction = workflow.run(method, validated, current_state)
                snapshot = State.model_validate(state.model_dump(mode="json"))
                result = EventResult(
                    status="accepted", instruction_id=self.instruction_id + 1
                )
                invocation = EventInvocation(
                    event=event,
                    node=self.state.node if self.state else None,
                    instruction_id=self.instruction_id,
                    arguments=validated.model_dump(mode="json"),
                    result=result,
                    client_info=client_info,
                )
                self._save(
                    instruction_id=self.instruction_id + 1,
                    state=snapshot,
                    instruction=instruction,
                    status="completed"
                    if workflow.graph.nodes[state.node].final
                    else "active",
                    history=[*self.history, invocation],
                )
                return result
            except EventRejected as exc:
                status = "not_ready" if isinstance(exc, NotReady) else "rejected"
                message = str(exc)
            except Exception as exc:
                logger.exception("Workflow %s event %s failed", workflow.id, event)
                status = "error"
                message = f"{type(exc).__name__}: {exc}. No session progress was saved."
        result = EventResult(
            status=status,
            instruction_id=self.instruction_id,
            message=message,
        )
        return self._record_event(event, instruction_id, result, client_info)

    def validate_workflow(self, workflow: Workflow) -> State:
        """Return a detached concrete state compatible with this workflow.

        Session loading validates the saved snapshot independently of workflow
        code. This method checks ownership, the current state model, node, and
        pending events without executing handlers or changing the session.
        """
        if self.workflow != workflow.id:
            raise WorkflowError("Session belongs to another workflow")
        if self.state is None:
            raise WorkflowError("Session has not started")
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
        if self.status == "active" and self.state is not None:
            try:
                self.validate_workflow(workflow)
            except (ValueError, WorkflowError) as exc:
                response.pop("instruction", None)
                response["message"] = f"Incompatible stored state: {exc}"
        return response

    @property
    def events(self) -> list[str]:
        if self.status != "active" or self.instruction is None:
            return []
        return [
            item["event"]
            for item in (self.instruction.events or {}).get("accepted", [])
        ]

    def _save(self, **changes: Any) -> None:
        """Persist prepared changes before applying them to this instance."""
        changes["updated_at"] = datetime.now(UTC)
        content = to_json({**self.model_dump(), **changes}, indent=2).decode() + "\n"
        atomic_write(self._path.parent / ".gitignore", "*\n")
        atomic_write(self._path, content)
        for name, value in changes.items():
            setattr(self, name, value)

    def _record_event(
        self,
        event: str,
        instruction_id: int,
        result: EventResult,
        client_info: ClientInfo | None,
    ) -> EventResult:
        """Record a failed event under the existing lock; preserve its result."""
        if self.state is None:
            return result
        try:
            invocation = EventInvocation(
                event=event,
                node=self.state.node,
                instruction_id=instruction_id,
                result=result,
                client_info=client_info,
            )
            self._save(history=[*self.history, invocation])
        except Exception:
            logger.warning("Could not record workflow event %s", event, exc_info=True)
        return result

    def _instruction_response(self) -> dict[str, Any]:
        response = {
            "session_id": self.id,
            "instruction_id": self.instruction_id,
            "session_status": self.status,
            "node": self.state.node if self.state else None,
        }
        if self.state is None or self.instruction is None:
            return {**response, "message": "Session has not started"}
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
        if self.state is None:
            return result
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
        """Cancel while holding the session lock, without loading workflow code."""
        if self._write_lock is None or not self._write_lock.is_locked:
            raise ValidationError("Session writes require 'with session.lock()'")
        if self.state is None:
            raise ValidationError("Session has not started")
        if self.status == "completed":
            raise ValidationError(f"Session {self.id!r} is already completed")
        self._save(status="cancelled")
        return {"session_id": self.id, "status": "cancelled", "note": CANCEL_NOTE}


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
        if (
            not session.history
            or session.instruction_id < 1
            or session.state is None
            or session.instruction is None
        ):
            raise ValidationError("Stored session must be started and have history")
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
