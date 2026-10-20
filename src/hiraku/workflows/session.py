"""Stored workflow executions and project-level session lookup."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from ..config import HirakuConfig
from ..errors import SessionNotFoundError, ValidationError
from ..io import atomic_write
from .workflow import EventResult, Instruction

if TYPE_CHECKING:
    from .workflow import Workflow

logger = logging.getLogger(__name__)
LOCK_TIMEOUT_SECONDS = 30
CANCEL_NOTE = (
    "Cancelling stops the workflow from accepting events. It does not revert "
    "code, delete records, or stop processes that are still running."
)


class HistoryEntry(BaseModel):
    """One accepted submission, addressing the preceding instruction."""

    instruction_id: int
    event: str
    arguments: dict[str, Any]
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Session(BaseModel):
    """A saved execution. The state owns its phase; instructions are snapshots."""

    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    id: str
    workflow: str
    status: Literal["active", "completed", "cancelled"] = "active"
    instruction_id: int = Field(default=0, ge=0)
    state: dict[str, Any]
    instruction: Instruction
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    _path: Path = PrivateAttr()

    @field_validator("state")
    @classmethod
    def _state_has_phase(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value.get("phase"), str):
            raise ValueError("Session state needs a string phase")
        return value

    @property
    def phase(self) -> str:
        return self.state["phase"]

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
            "phase": self.phase,
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
                workflow._check_session(self)
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
        if not isinstance(raw, dict) or raw.get("format_version") != 1:
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
