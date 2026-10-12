"""Transport-independent workflow operations shared by MCP and the CLI."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from ..config import HirakuConfig
from ..errors import HirakuError, NotReady, ValidationError, WorkflowError
from .catalog import Catalog, WorkflowEntry
from .core import Completion, Handler, Instruction, Session
from .sessions import (
    Receipt,
    SessionStore,
    StoredSession,
    Timeout,
    lock_timeout_message,
    validate_session_id,
)

logger = logging.getLogger(__name__)

GUIDE = """\
How to follow a Hiraku workflow session:
1. Carry out the instruction. Its context holds the data it refers to.
2. If requires_user_response is true, ask the user and wait for their answer
   before replying; otherwise decide yourself.
3. Continue by calling exactly one of the listed reply tools. Pass its fixed
   arguments unchanged and supply the parameters its schema asks for.
4. Each accepted reply returns the next instruction. Stop when a response has
   completed: true.
5. A response with status rejected or not_ready keeps the same instruction
   pending: fix the input, or check again later for not_ready.
6. To resume, call session_show with the session_id; it returns the pending
   instruction. Do not start a new session to resume an existing one.
Before repeating consequential work such as creating records or launching
experiments, check whether it already exists.\
"""

_CANCEL_NOTE = (
    "Cancelling stops the workflow from accepting replies. It does not revert "
    "code, delete records, or stop processes that are still running."
)


class WorkflowService:
    """Start, continue, inspect, and cancel workflow sessions.

    Args:
        config: Project configuration.
        catalog: Loaded workflows; created from ``config`` when omitted.
        exposed: Workflow IDs to expose instead of the configured set, e.g. from
            ``hiraku mcp serve --workflow``.

    Raises:
        hiraku.errors.WorkflowError: If the configured or requested workflow set
            names unknown workflows.
    """

    def __init__(
        self,
        config: HirakuConfig,
        catalog: Catalog | None = None,
        *,
        exposed: list[str] | None = None,
    ) -> None:
        self.config = config
        self.catalog = catalog or Catalog(config)
        self.store = SessionStore(config)
        self._override = list(exposed) if exposed is not None else None
        self._check_known(self.exposed_ids())

    # Exposure -----------------------------------------------------------------

    def exposed_ids(self) -> list[str]:
        """Return the IDs of workflows whose tools are exposed."""
        if self._override is not None:
            return list(self._override)
        configured = self.config.mcp.workflows if self.config.mcp else None
        if configured is None:
            return list(self.catalog.entries)
        return list(configured)

    def _check_known(self, identifiers: list[str]) -> None:
        unknown = [item for item in identifiers if item not in self.catalog.entries]
        if unknown:
            available = ", ".join(self.catalog.entries) or "none"
            raise WorkflowError(
                f"Unknown workflows in the MCP workflow set: {', '.join(unknown)}; "
                f"available workflows: {available}. Fix mcp.workflows in "
                f"{self.config.path}."
            )

    def activate(self, workflow_id: str) -> dict[str, Any]:
        """Expose a workflow's tools and persist the workflow set."""
        self.catalog.entry(workflow_id)
        current = self.exposed_ids()
        if workflow_id in current:
            return self._activation_result(current, changed=False)
        return self._set_exposed([*current, workflow_id])

    def deactivate(self, workflow_id: str) -> dict[str, Any]:
        """Hide a workflow's tools and persist the workflow set.

        Sessions are never modified; they can be inspected while inactive.
        """
        self.catalog.entry(workflow_id)
        current = self.exposed_ids()
        if workflow_id not in current:
            return self._activation_result(current, changed=False)
        return self._set_exposed([item for item in current if item != workflow_id])

    def _set_exposed(self, workflows: list[str]) -> dict[str, Any]:
        if self._override is not None:
            self._override = list(workflows)
        self.config.set_mcp_workflows(list(workflows))
        return self._activation_result(workflows, changed=True)

    def _activation_result(
        self, workflows: list[str], *, changed: bool
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "active": workflows,
            "changed": changed,
            "saved": changed,
        }
        if changed:
            result["note"] = (
                "The workflow set was saved to hiraku.yml. New tools may only "
                "appear in your next message; if they do not appear, reconnect "
                "the Hiraku MCP server or restart the harness."
            )
        return result

    # Discovery ----------------------------------------------------------------

    def list_workflows(self) -> list[dict[str, Any]]:
        """Describe every discovered workflow, active or not."""
        exposed = set(self.exposed_ids())
        result = []
        for entry in self.catalog.entries.values():
            item: dict[str, Any] = {
                "id": entry.id,
                "active": entry.id in exposed,
                "description": entry.description,
            }
            if entry.error is not None:
                item["error"] = entry.error
            else:
                start = entry.handlers["start"]
                item["start_tool"] = entry.tool_name("start")
                item["start_arguments"] = start.arguments.model_json_schema()
            result.append(item)
        return result

    def tool_definitions(self, *, exposed_only: bool = True) -> list[dict[str, Any]]:
        """Return MCP tool definitions of workflow handlers."""
        exposed = set(self.exposed_ids())
        definitions = []
        for name, entry, handler in self.catalog.tools():
            if exposed_only and entry.id not in exposed:
                continue
            definitions.append(
                {
                    "name": name,
                    "description": _tool_description(entry, handler),
                    "input_schema": input_schema(handler),
                }
            )
        return definitions

    def is_exposed_tool(self, name: str) -> bool:
        """Return whether a workflow tool belongs to an exposed workflow."""
        try:
            entry, _ = self.catalog.tool(name)
        except WorkflowError:
            return False
        return entry.id in self.exposed_ids()

    # Invocation ---------------------------------------------------------------

    def call(self, tool_name: str, arguments: Mapping[str, Any] | None) -> Any:
        """Invoke a workflow tool by name.

        Returns:
            A JSON-compatible response. Continuation responses carry a
            ``status`` of ``accepted``, ``duplicate``, ``conflict``, ``stale``,
            ``rejected``, ``not_ready``, ``blocked``, or ``error``.
        """
        entry, handler = self.catalog.tool(tool_name)
        if arguments is not None and not isinstance(arguments, Mapping):
            raise ValidationError("Tool arguments must be a JSON object")
        values = dict(arguments or {})
        if handler.kind == "start":
            return self._start(entry, handler, values)
        if handler.kind == "continuation":
            return self._continue(entry, handler, values)
        return self._ordinary(entry, handler, values)

    def _ordinary(
        self, entry: WorkflowEntry, handler: Handler, values: dict[str, Any]
    ) -> Any:
        try:
            validated = handler.arguments.model_validate(values)
        except PydanticValidationError as exc:
            return {"status": "rejected", "message": _validation_message(exc)}
        workflow = self.catalog.instantiate(entry.id)
        try:
            result = handler.function(workflow, **_keywords(validated))
        except (HirakuError, PydanticValidationError) as exc:
            return {"status": "rejected", "message": str(exc)}
        except Exception as exc:
            return _unexpected(entry, handler, exc)
        return {"status": "ok", "result": _jsonable(result)}

    def _start(
        self, entry: WorkflowEntry, handler: Handler, values: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            validated = handler.arguments.model_validate(values)
        except PydanticValidationError as exc:
            return {"status": "rejected", "message": _validation_message(exc)}
        workflow = self.catalog.instantiate(entry.id)
        try:
            response = handler.function(workflow, **_keywords(validated))
        except NotReady as exc:
            return {"status": "not_ready", "message": str(exc)}
        except (HirakuError, PydanticValidationError) as exc:
            return {"status": "rejected", "message": str(exc)}
        except Exception as exc:
            return _unexpected(entry, handler, exc)
        try:
            session = _checked_response(entry, response)
        except WorkflowError as exc:
            return {"status": "error", "message": str(exc)}
        session_id = self.store.new_id(entry.id)
        now = datetime.now(UTC)
        orientation = self.catalog.prompts.document(entry.id).orientation
        stored = StoredSession(
            session_id=session_id,
            workflow=entry.id,
            status="active",
            instruction_id=0,
            data={},
            orientation=orientation,
            last_response={},
            created_at=now,
            updated_at=now,
        )
        self._apply(entry, stored, response, session)
        with self.store.lock(session_id):
            self.store.save(stored)
        return {
            "status": "accepted",
            "guide": GUIDE,
            "orientation": orientation,
            **stored.last_response,
        }

    def _continue(
        self, entry: WorkflowEntry, handler: Handler, values: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = validate_session_id(values.pop("session_id", None))
        instruction_id = values.pop("instruction_id", None)
        if isinstance(instruction_id, bool) or not isinstance(instruction_id, int):
            raise ValidationError("instruction_id must be an integer")
        try:
            with self.store.lock(session_id):
                stored = self.store.load(session_id)
                return self._continue_locked(
                    entry, handler, stored, instruction_id, values
                )
        except Timeout:
            return {"status": "busy", "message": lock_timeout_message(session_id)}

    def _continue_locked(
        self,
        entry: WorkflowEntry,
        handler: Handler,
        stored: StoredSession,
        instruction_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if stored.workflow != entry.id:
            return _outcome(
                stored,
                "rejected",
                f"Session {stored.session_id!r} belongs to workflow "
                f"{stored.workflow!r}, not {entry.id!r}",
            )
        try:
            validated = handler.arguments.model_validate(values)
        except PydanticValidationError as exc:
            return _outcome(stored, "rejected", _validation_message(exc))
        call_arguments = validated.model_dump(mode="json")
        receipt = stored.last_receipt
        if receipt is not None and receipt.instruction_id == instruction_id:
            if receipt.handler == handler.name and receipt.arguments == call_arguments:
                return _outcome(
                    stored,
                    "duplicate",
                    f"Instruction {instruction_id} was already answered by this "
                    "call; returning the current response.",
                )
            return _outcome(
                stored,
                "conflict",
                f"Instruction {instruction_id} was already answered by "
                f"{entry.tool_name(receipt.handler)} with "
                f"{receipt.arguments}; this call was not applied.",
            )
        if stored.status != "active":
            return _outcome(
                stored, "rejected", f"Session is {stored.status}; it accepts no replies"
            )
        if instruction_id < stored.instruction_id:
            return _outcome(
                stored,
                "stale",
                f"Instruction {instruction_id} is no longer pending; this call "
                f"was not applied. The pending instruction is "
                f"{stored.instruction_id}.",
            )
        if instruction_id > stored.instruction_id:
            return _outcome(
                stored,
                "rejected",
                f"Instruction {instruction_id} has not been issued; the pending "
                f"instruction is {stored.instruction_id}.",
            )
        if handler.name not in stored.pending_replies:
            offered = ", ".join(
                entry.tool_name(name) for name in stored.pending_replies
            )
            return _outcome(
                stored,
                "rejected",
                f"{entry.tool_name(handler.name)} is not a reply to the pending "
                f"instruction; use one of: {offered}",
            )
        problem = compatibility_problem(entry, stored)
        if problem is not None:
            return _outcome(stored, "blocked", problem)
        assert handler.session_model is not None
        session = handler.session_model.model_validate(stored.data)
        session._session_id = stored.session_id
        workflow = self.catalog.instantiate(entry.id)
        keywords = _keywords(validated)
        assert handler.session_parameter is not None
        keywords[handler.session_parameter] = session
        try:
            response = handler.function(workflow, **keywords)
        except NotReady as exc:
            return _outcome(stored, "not_ready", str(exc))
        except (HirakuError, PydanticValidationError) as exc:
            return _outcome(stored, "rejected", str(exc))
        except Exception as exc:
            return {**_unexpected(entry, handler, exc), **stored.last_response}
        try:
            result_session = _checked_response(entry, response)
        except WorkflowError as exc:
            return _outcome(stored, "error", str(exc))
        stored.last_receipt = Receipt(
            instruction_id=instruction_id,
            handler=handler.name,
            arguments=call_arguments,
        )
        self._apply(entry, stored, response, result_session)
        self.store.save(stored)
        return {"status": "accepted", **stored.last_response}

    def _apply(
        self,
        entry: WorkflowEntry,
        stored: StoredSession,
        response: Instruction | Completion,
        session: Session,
    ) -> None:
        stored.data = session.model_dump(mode="json")
        stored.instruction_id += 1
        if isinstance(response, Completion):
            stored.status = "completed"
            stored.pending_replies = []
            stored.last_response = {
                "session_id": stored.session_id,
                "completed": True,
                "result": response.result,
            }
            return
        stored.pending_replies = list(response.replies)
        replies = []
        for name in response.replies:
            reply = entry.handlers[name]
            replies.append(
                {
                    "tool": entry.tool_name(name),
                    "arguments": {
                        "session_id": stored.session_id,
                        "instruction_id": stored.instruction_id,
                    },
                    "description": _summary(reply.description),
                }
            )
        stored.last_response = {
            "session_id": stored.session_id,
            "instruction_id": stored.instruction_id,
            "instruction": {
                "text": response.text,
                "context": response.context,
                "requires_user_response": response.requires_user_response,
                "replies": replies,
            },
        }

    # Inspection and cancellation ---------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summarize stored sessions, newest first."""
        summaries: list[dict[str, Any]] = []
        for item in self.store.list_all():
            if isinstance(item, tuple):
                summaries.append({"session_id": item[0], "error": item[1]})
                continue
            summaries.append(
                {
                    "session_id": item.session_id,
                    "workflow": item.workflow,
                    "status": item.status,
                    "instruction_id": item.instruction_id,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                }
            )
        return sorted(
            summaries, key=lambda item: item.get("updated_at", ""), reverse=True
        )

    def show(self, session_id: str) -> dict[str, Any]:
        """Inspect a session from its stored envelope.

        Works when the workflow is missing, inactive, or can no longer load the
        stored data.
        """
        stored = self.store.load(validate_session_id(session_id))
        entry = self.catalog.entries.get(stored.workflow)
        result: dict[str, Any] = {
            "session_id": stored.session_id,
            "workflow": stored.workflow,
            "status": stored.status,
            "instruction_id": stored.instruction_id,
            "created_at": stored.created_at.isoformat(),
            "updated_at": stored.updated_at.isoformat(),
            "guide": GUIDE,
            "orientation": stored.orientation,
            "data": stored.data,
            "last_response": stored.last_response,
            "workflow_available": entry is not None and entry.loaded,
            "workflow_active": stored.workflow in self.exposed_ids(),
        }
        if stored.status == "cancelled":
            result["note"] = (
                "This session is cancelled. Its last instruction is no longer "
                "actionable."
            )
        elif stored.status == "active":
            if entry is None or not entry.loaded:
                reason = entry.error if entry is not None else "it is not installed"
                result["note"] = (
                    f"Workflow {stored.workflow!r} is unavailable ({reason}); the "
                    "session cannot continue until it loads again."
                )
            else:
                problem = compatibility_problem(entry, stored)
                if problem is not None:
                    result["compatibility"] = problem
                elif stored.workflow not in self.exposed_ids():
                    result["note"] = (
                        f"Workflow {stored.workflow!r} is not active. Activate it "
                        "with workflow_activate to continue this session."
                    )
        return result

    def cancel(self, session_id: str) -> dict[str, Any]:
        """Cancel a session, preserving every research artifact."""
        validate_session_id(session_id)
        try:
            with self.store.lock(session_id):
                stored = self.store.load(session_id)
                if stored.status == "completed":
                    raise ValidationError(
                        f"Session {session_id!r} is already completed"
                    )
                if stored.status == "active":
                    stored.status = "cancelled"
                    stored.pending_replies = []
                    self.store.save(stored)
        except Timeout as exc:
            raise ValidationError(lock_timeout_message(session_id)) from exc
        return {
            "session_id": stored.session_id,
            "status": stored.status,
            "note": _CANCEL_NOTE,
        }

    def session_exists(self, session_id: str) -> bool:
        """Return whether a session exists."""
        try:
            return self.store.exists(session_id)
        except ValidationError:
            return False


def compatibility_problem(entry: WorkflowEntry, stored: StoredSession) -> str | None:
    """Check a stored session against the loaded workflow implementation.

    Returns:
        A description of the incompatibility, or ``None`` if the session can
        continue. Continuation uses the currently loaded implementation; authors
        are responsible for keeping it compatible with existing sessions.
    """
    missing = [
        name
        for name in stored.pending_replies
        if name not in entry.handlers or entry.handlers[name].kind != "continuation"
    ]
    if missing:
        return (
            "The loaded workflow no longer defines the pending replies "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    if entry.session_model is None:
        return "The loaded workflow defines no continuation handlers."
    try:
        entry.session_model.model_validate(stored.data)
    except PydanticValidationError as exc:
        return (
            "The stored session data no longer matches the loaded workflow's "
            f"session model: {_validation_message(exc)}. Inspect the session and "
            "start a new one."
        )
    return None


def input_schema(handler: Handler) -> dict[str, Any]:
    """Return the MCP input schema of a handler, including correlation fields."""
    schema = handler.arguments.model_json_schema()
    schema.pop("title", None)
    if handler.kind != "continuation":
        return schema
    properties = {
        "session_id": {
            "type": "string",
            "description": "Fixed argument from the pending instruction.",
        },
        "instruction_id": {
            "type": "integer",
            "description": "Fixed argument from the pending instruction.",
        },
        **schema.get("properties", {}),
    }
    return {
        **schema,
        "type": "object",
        "properties": properties,
        "required": ["session_id", "instruction_id", *schema.get("required", [])],
    }


def _tool_description(entry: WorkflowEntry, handler: Handler) -> str:
    description = handler.description or f"{handler.name} of workflow {entry.id}"
    if handler.kind == "start":
        workflow = _summary(entry.description)
        prefix = f"Start a {entry.id} workflow session."
        if workflow:
            prefix += f" {workflow}"
        return f"{prefix}\n\n{description}"
    if handler.kind == "continuation":
        return (
            f"{description}\n\nReply to a pending {entry.id} instruction. Call only "
            "when the pending instruction lists this tool."
        )
    return description


def _summary(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].replace("\n", " ") if text else ""


def _keywords(validated: BaseModel) -> dict[str, Any]:
    return {name: getattr(validated, name) for name in type(validated).model_fields}


def _checked_response(entry: WorkflowEntry, response: Any) -> Session:
    if not isinstance(response, Instruction | Completion):
        raise WorkflowError(
            f"Workflow {entry.id!r} returned {type(response).__name__}; handlers "
            "must return self.instruction(...) or self.completion(...)"
        )
    session = response.session
    model = entry.session_model
    if model is not None and type(session) is not model:
        raise WorkflowError(
            f"Workflow {entry.id!r} returned a {type(session).__name__} session; "
            f"expected {model.__name__}"
        )
    if isinstance(response, Instruction):
        unknown = [name for name in response.replies if name not in entry.handlers]
        if unknown:
            raise WorkflowError(f"Unknown replies: {', '.join(unknown)}")
    try:
        type(session).model_validate(session.model_dump(mode="json"))
    except PydanticValidationError as exc:
        raise WorkflowError(
            f"Session of workflow {entry.id!r} does not round-trip through JSON: "
            f"{_validation_message(exc)}"
        ) from exc
    return session


def _outcome(stored: StoredSession, status: str, message: str) -> dict[str, Any]:
    return {"status": status, "message": message, **stored.last_response}


def _unexpected(
    entry: WorkflowEntry, handler: Handler, exc: Exception
) -> dict[str, Any]:
    logger.exception("Workflow %s handler %s failed", entry.id, handler.name)
    return {
        "status": "error",
        "message": (
            f"{entry.tool_name(handler.name)} failed unexpectedly with "
            f"{type(exc).__name__}: {exc}. No session progress was saved."
        ),
    }


def _validation_message(exc: PydanticValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "input"
        parts.append(f"{location}: {error.get('msg')}")
    return "; ".join(parts)


def _jsonable(value: Any) -> Any:
    return to_jsonable_python(value)


__all__ = [
    "GUIDE",
    "WorkflowService",
    "compatibility_problem",
    "input_schema",
]
