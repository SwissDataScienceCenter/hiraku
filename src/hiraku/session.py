"""Workflow sessions: the stored state of one workflow invocation."""

from __future__ import annotations

import inspect
import json
import re
import secrets
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self, TypeVar

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from pydantic import ValidationError as PydanticValidationError

from .errors import SessionNotFoundError, ValidationError
from .io import atomic_write

if TYPE_CHECKING:
    from .config import HirakuConfig
    from .workflow import Workflow

LOCK_TIMEOUT_SECONDS = 30.0
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

SessionStatus = Literal["active", "completed", "cancelled"]
_S = TypeVar("_S", bound="Session")

CANCEL_NOTE = (
    "Cancelling stops the workflow from accepting replies. It does not revert "
    "code, delete records, or stop processes that are still running."
)


class HistoryEntry(BaseModel):
    """One accepted call of a session: its start or a reply."""

    model_config = ConfigDict(extra="forbid")
    instruction_id: int
    reply: str
    arguments: dict[str, Any]
    at: datetime


class Session(BaseModel):
    """The stored state of one workflow invocation.

    Subclasses declare typed fields for the workflow's own data: resolved
    arguments, record references, counters, and results. Sessions are stored as
    JSON in ``hiraku/sessions/``, so fields must be JSON-serializable.

    The base fields and methods belong to the runtime and are reserved:
    subclasses cannot redefine them, and changes a tool makes to the base
    fields are discarded.

    Attributes:
        id: Session ID, assigned when the session is first saved.
        workflow: ID of the workflow that owns the session.
        status: ``active`` until it completes or is cancelled.
        instruction_id: Number of the pending instruction; replies must name it.
        replies: Reply tools allowed for the pending instruction.
        response: The last response, returned again for retries and by `show`.
        history: Accepted calls, oldest first: the start, then each reply. The
            last entry recognizes retries.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = ""
    workflow: str = ""
    status: SessionStatus = "active"
    instruction_id: int = Field(default=0, ge=0)
    replies: list[str] = Field(default_factory=list)
    response: dict[str, Any] = Field(default_factory=dict)
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    _config: HirakuConfig | None = PrivateAttr(default=None)
    _stored: dict[str, Any] = PrivateAttr(default_factory=dict)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        names = set(inspect.get_annotations(cls)) | set(vars(cls))
        reserved = sorted(names & RESERVED)
        if reserved:
            raise TypeError(
                f"{cls.__name__} redefines reserved Session names: "
                f"{', '.join(reserved)}"
            )

    @property
    def config(self) -> HirakuConfig:
        """Return the project of a loaded or saved session."""
        if self._config is None:
            raise ValidationError(f"Session {self.id or '(new)'} has no project")
        return self._config

    # Storage --------------------------------------------------------------------

    @classmethod
    def load(cls, session_id: str, *, config: HirakuConfig) -> Self:
        """Load a stored session.

        The base `Session` reads only the runtime fields, so any session can be
        inspected without its workflow's code; a subclass reads all fields.

        Raises:
            hiraku.errors.SessionNotFoundError: If the session does not exist.
            hiraku.errors.ValidationError: If the stored data does not match.
        """
        return cls._validate_stored(session_id, _read(session_id, config), config)

    def typed(self, model: type[_S]) -> _S:
        """Return this stored session as a workflow's own session model.

        Raises:
            hiraku.errors.ValidationError: If the stored data does not match.
        """
        return model._validate_stored(self.id, self._stored, self.config)

    @classmethod
    def _validate_stored(
        cls, session_id: str, raw: dict[str, Any], config: HirakuConfig
    ) -> Self:
        data = (
            raw
            if cls is not Session
            else {key: value for key, value in raw.items() if key in BASE_FIELDS}
        )
        try:
            session = cls.model_validate(data)
        except PydanticValidationError as exc:
            raise ValidationError(
                f"Session {session_id!r} does not match {cls.__name__}: {exc}"
            ) from exc
        session._config = config
        session._stored = raw
        return session

    @classmethod
    def list_all(cls, *, config: HirakuConfig) -> list[dict[str, Any]]:
        """Summarize every stored session, newest first."""
        directory = config.sessions_dir
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        summaries: list[dict[str, Any]] = []
        for path in paths:
            try:
                session = Session.load(path.stem, config=config)
            except (SessionNotFoundError, ValidationError) as exc:
                summaries.append({"session_id": path.stem, "error": str(exc)})
                continue
            summaries.append(
                {
                    "session_id": session.id,
                    "workflow": session.workflow,
                    "status": session.status,
                    "instruction_id": session.instruction_id,
                    "created_at": _isoformat(session.created_at),
                    "updated_at": _isoformat(session.updated_at),
                }
            )
        return sorted(
            summaries, key=lambda item: item.get("updated_at") or "", reverse=True
        )

    def save(self, *, config: HirakuConfig | None = None) -> None:
        """Atomically store the session, assigning it to ``config`` if given.

        Raises:
            hiraku.errors.ValidationError: If the session cannot be written.
        """
        if config is not None:
            self._config = config
        self.updated_at = datetime.now(UTC)
        data = self.model_dump(mode="json")
        _write(self.id, data, self.config)
        self._stored = data

    # Inspection and cancellation --------------------------------------------------

    def show(self, workflow: Workflow | None = None) -> dict[str, Any]:
        """Describe the session: status, pending instruction or result, and data.

        Pass the loaded workflow to include its orientation and to check that
        the session can still continue with the current code.
        """
        result: dict[str, Any] = {
            "session_id": self.id,
            "workflow": self.workflow,
            "status": self.status,
            "instruction_id": self.instruction_id,
            "created_at": _isoformat(self.created_at),
            "updated_at": _isoformat(self.updated_at),
            "data": {
                key: value
                for key, value in self._stored.items()
                if key not in BASE_FIELDS
            },
            "response": self.response,
        }
        if workflow is not None:
            result["orientation"] = workflow.orientation
        if self.status == "cancelled":
            result["note"] = (
                "This session is cancelled. Its last instruction is no longer "
                "actionable."
            )
        elif self.status == "active" and workflow is not None:
            from .workflow import compatibility_problem

            problem = compatibility_problem(workflow, self)
            if problem is not None:
                result["compatibility"] = problem
        elif self.status == "active":
            result["note"] = (
                f"Workflow {self.workflow!r} is unavailable; the session cannot "
                "continue until it loads. Fix the workflow and reconnect the Hiraku "
                "MCP server."
            )
        return result

    def cancel(self) -> dict[str, Any]:
        """Cancel the session, keeping every research artifact.

        Works without the workflow's code; fields the base class does not read
        are kept as stored.

        Raises:
            hiraku.errors.ValidationError: If the session is completed or busy.
        """
        try:
            with locked(self.id, config=self.config):
                raw = _read(self.id, self.config)
                if raw.get("status") == "completed":
                    raise ValidationError(f"Session {self.id!r} is already completed")
                if raw.get("status") == "active":
                    raw.update(
                        status="cancelled",
                        replies=[],
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                    _write(self.id, raw, self.config)
        except Timeout as exc:
            raise ValidationError(_busy(self.id)) from exc
        self.status = "cancelled"
        self.replies = []
        return {"session_id": self.id, "status": "cancelled", "note": CANCEL_NOTE}

    # Replies ----------------------------------------------------------------------

    def check(
        self, reply: str, instruction_id: int, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Decide whether a reply may run against the pending instruction.

        Returns:
            ``None`` if the reply answers the pending instruction; otherwise the
            response to return instead: the stored response for an identical
            retry, or a ``stale`` or ``rejected`` status.
        """
        last = self.history[-1] if self.history else None
        if last is not None and last.instruction_id == instruction_id:
            if last.reply == reply and last.arguments == arguments:
                return {"status": "accepted", **self.response}
            return self.outcome(
                "stale",
                f"Instruction {instruction_id} was already answered with reply "
                f"{last.reply!r} and arguments {last.arguments}; this call was not "
                "applied.",
            )
        if self.status != "active":
            return self.outcome(
                "rejected", f"Session is {self.status}; it accepts no replies"
            )
        if instruction_id != self.instruction_id:
            return self.outcome(
                "stale",
                f"Instruction {instruction_id} is not pending; this call was not "
                f"applied. The pending instruction is {self.instruction_id}.",
            )
        if reply not in self.replies:
            return self.outcome(
                "rejected",
                f"{reply!r} is not a reply to the pending instruction; use one of: "
                f"{', '.join(self.replies)}",
            )
        return None

    def advance(
        self,
        response: Instruction | Completion,
        replies: Mapping[str, dict[str, Any]],
        call: HistoryEntry,
    ) -> None:
        """Record an accepted call and the response it led to.

        Args:
            response: The instruction or completion a tool returned.
            replies: Description and argument schema of each of the workflow's
                replies, by name.
            call: The accepted call, appended to the history.
        """
        self.history = [*self.history, call]
        self.instruction_id += 1
        if isinstance(response, Completion):
            self.status = "completed"
            self.replies = []
            self.response = {
                "session_id": self.id,
                "completed": True,
                "result": response.result,
            }
            return
        self.replies = list(response.replies)
        self.response = {
            "session_id": self.id,
            "instruction_id": self.instruction_id,
            "instruction": {
                "text": response.text,
                "context": response.context,
                "requires_user_response": response.requires_user_response,
                "replies": [
                    {"reply": name, **replies.get(name, {})}
                    for name in response.replies
                ],
            },
        }

    def outcome(self, status: str, message: str) -> dict[str, Any]:
        """Return a status that leaves the session unchanged, with its response."""
        return {"status": status, "message": message, **self.response}


BASE_FIELDS = tuple(Session.model_fields)
RESERVED = frozenset(
    {
        *BASE_FIELDS,
        "config",
        "load",
        "list_all",
        "save",
        "show",
        "cancel",
        "check",
        "advance",
        "outcome",
        "typed",
    }
)


@dataclass(frozen=True)
class Instruction:
    """The next request to the harness, returned by a workflow tool.

    Attributes:
        session: Session whose state accompanies the instruction.
        text: Actionable guidance, usually taken from ``workflow.md``.
        replies: Names of the replies that may answer it.
        context: Concise JSON-compatible data for the harness.
        requires_user_response: Whether the harness must obtain a user answer
            before replying. This is a conversational contract only.
    """

    session: Session
    text: str
    replies: tuple[str, ...]
    context: Any = None
    requires_user_response: bool = False


@dataclass(frozen=True)
class Completion:
    """A terminal response: the invocation ended.

    Attributes:
        session: Final session state.
        result: Workflow-specific JSON-compatible result. Completion means the
            invocation ended, not necessarily that every objective was met.
    """

    session: Session
    result: Any = None


Response = Instruction | Completion


def new_session_id(workflow_id: str) -> str:
    """Return a new opaque session ID for a workflow."""
    return f"{workflow_id}-{secrets.token_hex(6)}"


def validate_session_id(session_id: Any) -> str:
    """Validate a session ID before it is used to resolve a storage path.

    Raises:
        hiraku.errors.ValidationError: If the ID is malformed.
    """
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValidationError(f"Invalid session ID: {session_id!r}")
    return session_id


@contextmanager
def locked(session_id: str, *, config: HirakuConfig) -> Generator[None]:
    """Hold the inter-process lock guarding one session's changes.

    Raises:
        filelock.Timeout: If another call holds the lock for too long.
    """
    _ensure_directory(config)
    lock = FileLock(
        str(config.sessions_dir / f".{validate_session_id(session_id)}.lock"),
        timeout=LOCK_TIMEOUT_SECONDS,
    )
    with lock:
        yield


def _read(session_id: str, config: HirakuConfig) -> dict[str, Any]:
    path = config.sessions_dir / f"{validate_session_id(session_id)}.json"
    if not path.is_file():
        raise SessionNotFoundError(f"Session {session_id!r} not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValidationError(f"Session file {path} is malformed: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("id") != session_id:
        raise ValidationError(f"Session file {path} is malformed")
    return raw


def _write(session_id: str, data: dict[str, Any], config: HirakuConfig) -> None:
    _ensure_directory(config)
    path = config.sessions_dir / f"{validate_session_id(session_id)}.json"
    content = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        atomic_write(path, content + "\n")
    except OSError as exc:
        raise ValidationError(f"Cannot save session {session_id!r}: {exc}") from exc


def _ensure_directory(config: HirakuConfig) -> None:
    directory = config.sessions_dir
    if directory.is_dir():
        return
    directory.mkdir(parents=True, exist_ok=True)
    # Sessions are local conversational state and are not meant for Git.
    ignore = directory / ".gitignore"
    if not ignore.exists():
        atomic_write(ignore, "*\n")


def _busy(session_id: str) -> str:
    return f"Session {session_id!r} is busy with another call; retry after it finishes"


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "CANCEL_NOTE",
    "Completion",
    "Instruction",
    "HistoryEntry",
    "Response",
    "Session",
    "Timeout",
    "locked",
    "new_session_id",
    "validate_session_id",
]
