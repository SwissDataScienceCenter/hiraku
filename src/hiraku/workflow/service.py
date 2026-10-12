"""Transport-independent workflow operations shared by MCP and the CLI."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from filelock import FileLock
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from ..config import HirakuConfig
from ..errors import HirakuError, NotReady, ValidationError, WorkflowError
from .core import (
    Completion,
    Handler,
    Instruction,
    Session,
    Workflow,
    discover_workflows,
    handlers,
    load_workflow,
    prompts,
    resources,
    session_model,
)
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

    Every discovered workflow is loaded once, when the service is created. A
    workflow that fails to load is logged and left out; `activate` loads it
    again and reports the error.

    Args:
        config: Project configuration.
        exposed: Workflow IDs to expose instead of the configured set, e.g. from
            ``hiraku mcp --workflow``.

    Raises:
        hiraku.errors.WorkflowError: If the configured or requested workflow set
            names unknown workflows.
    """

    def __init__(
        self, config: HirakuConfig, *, exposed: list[str] | None = None
    ) -> None:
        self.config = config
        self.store = SessionStore(config)
        self.workflows: dict[str, Workflow] = {}
        self._discovered = discover_workflows(config)
        self._override = list(exposed) if exposed is not None else None
        unknown = [item for item in self.exposed_ids() if item not in self._discovered]
        if unknown:
            raise WorkflowError(
                f"Unknown workflows in the MCP workflow set: {', '.join(unknown)}; "
                f"available workflows: {', '.join(self._discovered) or 'none'}. "
                f"Fix mcp.workflows in {self.config.path}."
            )
        for workflow_id in self._discovered:
            try:
                self.workflow(workflow_id)
            except Exception as exc:
                logger.warning("Workflow %s is unavailable: %s", workflow_id, exc)

    def workflow(self, workflow_id: str) -> Workflow:
        """Return a loaded workflow, loading it if it is not loaded yet.

        Raises:
            hiraku.errors.HirakuError: If the workflow does not exist or fails
                to load.
        """
        if workflow_id not in self.workflows:
            available = discover_workflows(self.config)
            if workflow_id not in available:
                raise WorkflowError(
                    f"Unknown workflow {workflow_id!r}; available workflows: "
                    f"{', '.join(available) or 'none'}"
                )
            self.workflows[workflow_id] = load_workflow(workflow_id, config=self.config)
        return self.workflows[workflow_id]

    # Exposure -----------------------------------------------------------------

    def exposed_ids(self) -> list[str]:
        """Return the IDs of workflows whose tools are exposed."""
        if self._override is not None:
            return list(self._override)
        configured = self.config.mcp.workflows if self.config.mcp else None
        if configured is None:
            return list(self._discovered)
        return list(configured)

    def _exposed(self) -> list[Workflow]:
        exposed = set(self.exposed_ids())
        return [
            workflow
            for workflow_id, workflow in sorted(self.workflows.items())
            if workflow_id in exposed
        ]

    def activate(self, workflow_id: str) -> dict[str, Any]:
        """Load a workflow, expose its tools, and persist the workflow set."""
        self.workflow(workflow_id)
        current = self.exposed_ids()
        if workflow_id in current:
            return self._activation_result(current, changed=False)
        return self._set_exposed([*current, workflow_id])

    def deactivate(self, workflow_id: str) -> dict[str, Any]:
        """Hide a workflow's tools and persist the workflow set.

        Sessions are never modified; they can be inspected while inactive.
        """
        current = self.exposed_ids()
        if workflow_id not in current:
            return self._activation_result(current, changed=False)
        return self._set_exposed([item for item in current if item != workflow_id])

    def _set_exposed(self, workflows: list[str]) -> dict[str, Any]:
        if self._override is not None:
            self._override = list(workflows)
        # Reload under a lock so concurrent CLI edits, such as a new active
        # record, are not overwritten by the server's startup snapshot.
        with FileLock(str(self.config.hiraku_dir / ".config.lock"), timeout=30):
            current = self.current_config()
            current.set_mcp_workflows(list(workflows))
        self.config.mcp = current.mcp
        return self._activation_result(workflows, changed=True)

    def current_config(self) -> HirakuConfig:
        """Reload the project configuration from disk.

        Long-running servers use this instead of their startup snapshot, so
        changes made elsewhere, such as the active record, are visible.
        """
        return HirakuConfig.load(self.config.path)

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
        """Describe every loaded workflow, active or not."""
        exposed = set(self.exposed_ids())
        return [
            {
                "id": workflow_id,
                "active": workflow_id in exposed,
                "description": workflow_description(workflow),
                "start_tool": tool_name(workflow_id, "start"),
                "start_arguments": handlers(type(workflow))[
                    "start"
                ].arguments.model_json_schema(),
            }
            for workflow_id, workflow in sorted(self.workflows.items())
        ]

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions of the exposed workflows' handlers."""
        return [
            {
                "name": tool_name(workflow.id, handler.name),
                "description": _tool_description(workflow, handler),
                "input_schema": input_schema(handler),
            }
            for workflow in self._exposed()
            for handler in sorted(
                handlers(type(workflow)).values(), key=lambda item: item.name
            )
        ]

    def tool(self, name: str) -> tuple[Workflow, Handler]:
        """Return the loaded workflow and handler behind an MCP tool name.

        Raises:
            hiraku.errors.WorkflowError: If no loaded workflow defines the tool.
        """
        workflow_id, _, handler_name = name.partition("_")
        workflow = self.workflows.get(workflow_id)
        handler = handlers(type(workflow)).get(handler_name) if workflow else None
        if workflow is None or handler is None:
            raise WorkflowError(f"Unknown workflow tool {name!r}")
        return workflow, handler

    def resource_definitions(self) -> list[dict[str, Any]]:
        """Return the MCP resources declared by exposed workflows."""
        return [
            {
                "uri": f"hiraku://workflows/{workflow.id}/{item.name}",
                "name": f"{workflow.id}-{item.name}",
                "description": _summary(item.description),
                "mime_type": item.mime_type,
            }
            for workflow in self._exposed()
            for item in resources(type(workflow)).values()
        ]

    def read_resource(self, workflow_id: str, name: str) -> tuple[str, str]:
        """Return the text and MIME type of a workflow-declared resource.

        Raises:
            hiraku.errors.WorkflowError: If the workflow is inactive or the
                resource does not exist or returns non-text.
        """
        workflow = self._exposed_workflow(workflow_id)
        item = resources(type(workflow)).get(name)
        if item is None:
            raise WorkflowError(f"Workflow {workflow_id!r} has no resource {name!r}")
        text = item.function(workflow.bind(self.current_config()))
        if not isinstance(text, str):
            raise WorkflowError(
                f"Resource {name!r} of {workflow_id!r} must return text"
            )
        return text, item.mime_type

    def prompt_definitions(self) -> list[dict[str, Any]]:
        """Return the MCP prompts declared by exposed workflows."""
        definitions = []
        for workflow in self._exposed():
            for item in prompts(type(workflow)).values():
                schema = item.arguments.model_json_schema()
                required = set(schema.get("required", []))
                definitions.append(
                    {
                        "name": tool_name(workflow.id, item.name),
                        "description": _summary(item.description),
                        "arguments": [
                            {
                                "name": key,
                                "description": spec.get("description"),
                                "required": key in required,
                            }
                            for key, spec in schema.get("properties", {}).items()
                        ],
                    }
                )
        return definitions

    def get_prompt(self, name: str, arguments: Mapping[str, Any] | None) -> str:
        """Render a workflow-declared MCP prompt.

        Raises:
            hiraku.errors.WorkflowError: If the prompt does not exist or is
                inactive, or its arguments are invalid.
        """
        workflow_id, _, prompt_name = name.partition("_")
        workflow = self.workflows.get(workflow_id)
        item = prompts(type(workflow)).get(prompt_name) if workflow else None
        if item is None:
            raise WorkflowError(f"Unknown workflow prompt {name!r}")
        workflow = self._exposed_workflow(workflow_id)
        try:
            validated = item.arguments.model_validate(dict(arguments or {}))
        except PydanticValidationError as exc:
            raise WorkflowError(_validation_message(exc)) from exc
        text = item.function(
            workflow.bind(self.current_config()), **_keywords(validated)
        )
        if not isinstance(text, str):
            raise WorkflowError(f"Prompt {name!r} must return text")
        return text

    def _exposed_workflow(self, workflow_id: str) -> Workflow:
        workflow = self.workflow(workflow_id)
        if workflow_id not in self.exposed_ids():
            raise WorkflowError(
                f"Workflow {workflow_id!r} is not active; call workflow_activate first"
            )
        return workflow

    # Invocation ---------------------------------------------------------------

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> Any:
        """Invoke a workflow tool by name.

        Returns:
            A JSON-compatible response. Continuation responses carry a
            ``status`` of ``accepted``, ``duplicate``, ``conflict``, ``stale``,
            ``rejected``, ``not_ready``, ``blocked``, or ``error``.
        """
        workflow, handler = self.tool(name)
        if arguments is not None and not isinstance(arguments, Mapping):
            raise ValidationError("Tool arguments must be a JSON object")
        values = dict(arguments or {})
        if handler.kind == "start":
            return self._start(workflow, handler, values)
        if handler.kind == "continuation":
            return self._continue(workflow, handler, values)
        return self._ordinary(workflow, handler, values)

    def _ordinary(
        self, workflow: Workflow, handler: Handler, values: dict[str, Any]
    ) -> Any:
        try:
            validated = handler.arguments.model_validate(values)
        except PydanticValidationError as exc:
            return {"status": "rejected", "message": _validation_message(exc)}
        bound = workflow.bind(self.current_config())
        try:
            result = handler.function(bound, **_keywords(validated))
        except (HirakuError, PydanticValidationError) as exc:
            return {"status": "rejected", "message": str(exc)}
        except Exception as exc:
            return _unexpected(workflow, handler, exc)
        return {"status": "ok", "result": _jsonable(result)}

    def _start(
        self, workflow: Workflow, handler: Handler, values: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            validated = handler.arguments.model_validate(values)
        except PydanticValidationError as exc:
            return {"status": "rejected", "message": _validation_message(exc)}
        bound = workflow.bind(self.current_config())
        try:
            response = handler.function(bound, **_keywords(validated))
        except NotReady as exc:
            return {"status": "not_ready", "message": str(exc)}
        except (HirakuError, PydanticValidationError) as exc:
            return {"status": "rejected", "message": str(exc)}
        except Exception as exc:
            return _unexpected(workflow, handler, exc)
        try:
            session = _checked_response(workflow, response)
        except WorkflowError as exc:
            return {"status": "error", "message": str(exc)}
        session_id = self.store.new_id(workflow.id)
        now = datetime.now(UTC)
        orientation = workflow.orientation
        stored = StoredSession(
            session_id=session_id,
            workflow=workflow.id,
            status="active",
            instruction_id=0,
            data={},
            orientation=orientation,
            last_response={},
            created_at=now,
            updated_at=now,
        )
        self._apply(workflow, stored, response, session)
        with self.store.lock(session_id):
            self.store.save(stored)
        return {
            "status": "accepted",
            "guide": GUIDE,
            "orientation": orientation,
            **stored.last_response,
        }

    def _continue(
        self, workflow: Workflow, handler: Handler, values: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = validate_session_id(values.pop("session_id", None))
        instruction_id = values.pop("instruction_id", None)
        if isinstance(instruction_id, bool) or not isinstance(instruction_id, int):
            raise ValidationError("instruction_id must be an integer")
        try:
            with self.store.lock(session_id):
                stored = self.store.load(session_id)
                return self._continue_locked(
                    workflow, handler, stored, instruction_id, values
                )
        except Timeout:
            return {"status": "busy", "message": lock_timeout_message(session_id)}

    def _continue_locked(
        self,
        workflow: Workflow,
        handler: Handler,
        stored: StoredSession,
        instruction_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if stored.workflow != workflow.id:
            return _outcome(
                stored,
                "rejected",
                f"Session {stored.session_id!r} belongs to workflow "
                f"{stored.workflow!r}, not {workflow.id!r}",
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
                f"{tool_name(workflow.id, receipt.handler)} with "
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
                tool_name(workflow.id, name) for name in stored.pending_replies
            )
            return _outcome(
                stored,
                "rejected",
                f"{tool_name(workflow.id, handler.name)} is not a reply to the pending "
                f"instruction; use one of: {offered}",
            )
        problem = compatibility_problem(workflow, stored)
        if problem is not None:
            return _outcome(stored, "blocked", problem)
        assert handler.session_model is not None
        session = handler.session_model.model_validate(stored.data)
        session._session_id = stored.session_id
        bound = workflow.bind(self.current_config())
        keywords = _keywords(validated)
        assert handler.session_parameter is not None
        keywords[handler.session_parameter] = session
        try:
            response = handler.function(bound, **keywords)
        except NotReady as exc:
            return _outcome(stored, "not_ready", str(exc))
        except (HirakuError, PydanticValidationError) as exc:
            return _outcome(stored, "rejected", str(exc))
        except Exception as exc:
            return {**_unexpected(workflow, handler, exc), **stored.last_response}
        try:
            result_session = _checked_response(workflow, response)
        except WorkflowError as exc:
            return _outcome(stored, "error", str(exc))
        stored.last_receipt = Receipt(
            instruction_id=instruction_id,
            handler=handler.name,
            arguments=call_arguments,
        )
        self._apply(workflow, stored, response, result_session)
        self.store.save(stored)
        return {"status": "accepted", **stored.last_response}

    def _apply(
        self,
        workflow: Workflow,
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
            reply = handlers(type(workflow))[name]
            replies.append(
                {
                    "tool": tool_name(workflow.id, name),
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
        workflow = self.workflows.get(stored.workflow)
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
            "workflow_available": workflow is not None,
            "workflow_active": stored.workflow in self.exposed_ids(),
        }
        if stored.status == "cancelled":
            result["note"] = (
                "This session is cancelled. Its last instruction is no longer "
                "actionable."
            )
        elif stored.status == "active":
            if workflow is None:
                result["note"] = (
                    f"Workflow {stored.workflow!r} is unavailable; the session "
                    "cannot continue until it loads. workflow_activate loads it "
                    "or reports why it fails."
                )
            else:
                problem = compatibility_problem(workflow, stored)
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


def compatibility_problem(workflow: Workflow, stored: StoredSession) -> str | None:
    """Check a stored session against the loaded workflow implementation.

    Returns:
        A description of the incompatibility, or ``None`` if the session can
        continue. Continuation uses the currently loaded implementation; authors
        are responsible for keeping it compatible with existing sessions.
    """
    registered = handlers(type(workflow))
    missing = [
        name
        for name in stored.pending_replies
        if name not in registered or registered[name].kind != "continuation"
    ]
    if missing:
        return (
            "The loaded workflow no longer defines the pending replies "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    model = session_model(type(workflow))
    if model is None:
        return "The loaded workflow defines no continuation handlers."
    try:
        model.model_validate(stored.data)
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


def tool_name(workflow_id: str, name: str) -> str:
    """Return the MCP name of a workflow tool or prompt."""
    return f"{workflow_id}_{name}"


def workflow_description(workflow: Workflow) -> str:
    """Return the ``description`` setting of a workflow, or its class docstring."""
    described = workflow.to_dict().get("description")
    return str(described or type(workflow).__doc__ or "").strip()


def _tool_description(workflow: Workflow, handler: Handler) -> str:
    description = handler.description or f"{handler.name} of workflow {workflow.id}"
    if handler.kind == "start":
        summary = _summary(workflow_description(workflow))
        prefix = f"Start a {workflow.id} workflow session."
        if summary:
            prefix += f" {summary}"
        return f"{prefix}\n\n{description}"
    if handler.kind == "continuation":
        return (
            f"{description}\n\nReply to a pending {workflow.id} instruction. Call "
            "only when the pending instruction lists this tool."
        )
    return description


def _summary(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].replace("\n", " ") if text else ""


def _keywords(validated: BaseModel) -> dict[str, Any]:
    return {name: getattr(validated, name) for name in type(validated).model_fields}


def _checked_response(workflow: Workflow, response: Any) -> Session:
    if not isinstance(response, Instruction | Completion):
        raise WorkflowError(
            f"Workflow {workflow.id!r} returned {type(response).__name__}; handlers "
            "must return self.instruction(...) or self.completion(...)"
        )
    session = response.session
    model = session_model(type(workflow))
    if model is not None and type(session) is not model:
        raise WorkflowError(
            f"Workflow {workflow.id!r} returned a {type(session).__name__} session; "
            f"expected {model.__name__}"
        )
    if isinstance(response, Instruction):
        registered = handlers(type(workflow))
        unknown = [name for name in response.replies if name not in registered]
        if unknown:
            raise WorkflowError(f"Unknown replies: {', '.join(unknown)}")
    try:
        type(session).model_validate(session.model_dump(mode="json"))
    except PydanticValidationError as exc:
        raise WorkflowError(
            f"Session of workflow {workflow.id!r} does not round-trip through JSON: "
            f"{_validation_message(exc)}"
        ) from exc
    return session


def _outcome(stored: StoredSession, status: str, message: str) -> dict[str, Any]:
    return {"status": status, "message": message, **stored.last_response}


def _unexpected(workflow: Workflow, handler: Handler, exc: Exception) -> dict[str, Any]:
    logger.exception("Workflow %s handler %s failed", workflow.id, handler.name)
    return {
        "status": "error",
        "message": (
            f"{tool_name(workflow.id, handler.name)} failed unexpectedly with "
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
    "tool_name",
    "workflow_description",
]
