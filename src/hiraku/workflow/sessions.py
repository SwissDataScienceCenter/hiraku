"""Persistent workflow session envelopes."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from ..config import HirakuConfig
from ..errors import SessionNotFoundError, ValidationError
from ..io import atomic_write

STORAGE_VERSION = 1
LOCK_TIMEOUT_SECONDS = 30.0
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

SessionStatus = Literal["active", "completed", "cancelled"]


class Receipt(BaseModel):
    """The last accepted continuation call of a session."""

    model_config = ConfigDict(extra="forbid")
    instruction_id: int
    handler: str
    arguments: dict[str, Any]


class StoredSession(BaseModel):
    """The persisted envelope of one workflow session.

    The envelope is readable without the workflow that created it, so sessions
    can be inspected and cancelled even when their workflow cannot be loaded.
    """

    model_config = ConfigDict(extra="forbid")
    session_id: str
    workflow: str
    storage_version: Literal[1] = STORAGE_VERSION
    status: SessionStatus
    instruction_id: int = Field(ge=0)
    pending_replies: list[str] = Field(default_factory=list)
    data: dict[str, Any]
    orientation: str = ""
    last_receipt: Receipt | None = None
    last_response: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def validate_session_id(session_id: Any) -> str:
    """Validate a session ID before it is used to resolve a storage path.

    Raises:
        hiraku.errors.ValidationError: If the ID is malformed.
    """
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValidationError(f"Invalid session ID: {session_id!r}")
    return session_id


class SessionStore:
    """Store session envelopes as ``<hiraku_dir>/sessions/<session-id>.json``.

    Args:
        config: Project configuration.
    """

    def __init__(self, config: HirakuConfig) -> None:
        self.directory = config.sessions_dir

    def new_id(self, workflow_id: str) -> str:
        """Return a new opaque session ID."""
        return f"{workflow_id}-{secrets.token_hex(6)}"

    def path(self, session_id: str) -> Path:
        """Return the storage path of a session."""
        return self.directory / f"{validate_session_id(session_id)}.json"

    def lock(self, session_id: str) -> FileLock:
        """Return the inter-process lock guarding one session's transitions."""
        self._ensure_directory()
        return FileLock(
            str(self.directory / f".{validate_session_id(session_id)}.lock"),
            timeout=LOCK_TIMEOUT_SECONDS,
        )

    def exists(self, session_id: str) -> bool:
        """Return whether a session file exists."""
        return self.path(session_id).is_file()

    def load(self, session_id: str) -> StoredSession:
        """Load and validate a session envelope.

        Raises:
            hiraku.errors.SessionNotFoundError: If the session does not exist.
            hiraku.errors.ValidationError: If the stored document is malformed.
        """
        path = self.path(session_id)
        if not path.is_file():
            raise SessionNotFoundError(f"Session {session_id!r} not found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = StoredSession.model_validate(raw)
        except (OSError, UnicodeError, ValueError, PydanticValidationError) as exc:
            raise ValidationError(f"Session file {path} is malformed: {exc}") from exc
        if stored.session_id != session_id:
            raise ValidationError(f"Session file {path} has a mismatched ID")
        return stored

    def save(self, stored: StoredSession) -> None:
        """Atomically persist a session envelope.

        Raises:
            hiraku.errors.ValidationError: If the session cannot be written.
        """
        stored.updated_at = datetime.now(UTC)
        self._ensure_directory()
        content = json.dumps(
            stored.model_dump(mode="json"), indent=2, ensure_ascii=False
        )
        try:
            atomic_write(self.path(stored.session_id), content + "\n")
        except OSError as exc:
            raise ValidationError(
                f"Cannot save session {stored.session_id!r}: {exc}"
            ) from exc

    def list_all(self) -> list[StoredSession | tuple[str, str]]:
        """Return stored sessions, or ``(session_id, error)`` for malformed files."""
        if not self.directory.is_dir():
            return []
        result: list[StoredSession | tuple[str, str]] = []
        for path in sorted(self.directory.glob("*.json")):
            session_id = path.stem
            try:
                result.append(self.load(session_id))
            except (ValidationError, SessionNotFoundError) as exc:
                result.append((session_id, str(exc)))
        return result

    def _ensure_directory(self) -> None:
        if self.directory.is_dir():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        # Sessions are local conversational state and are not meant for Git.
        ignore = self.directory / ".gitignore"
        if not ignore.exists():
            atomic_write(ignore, "*\n")


def lock_timeout_message(session_id: str) -> str:
    """Return the message used when a session lock cannot be acquired."""
    return f"Session {session_id!r} is busy with another call; retry after it finishes"


__all__ = [
    "LOCK_TIMEOUT_SECONDS",
    "Receipt",
    "SessionStore",
    "StoredSession",
    "Timeout",
    "lock_timeout_message",
    "validate_session_id",
]
