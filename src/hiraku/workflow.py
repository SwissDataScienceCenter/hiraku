"""Workflows: a ``workflow.md`` record whose tools run on stored sessions."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
import logging
import re
import sys
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeVar, overload

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    create_model,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .base import BaseRecord
from .errors import (
    HirakuError,
    NotReady,
    SubmissionRejected,
    ValidationError,
    WorkflowError,
)
from .markdown import ParsedMarkdown, parse_markdown, placeholders, render
from .session import (
    GUIDE,
    Completion,
    HistoryEntry,
    Instruction,
    Response,
    Session,
    Timeout,
    locked,
    new_session_id,
    validate_session_id,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver.prompts import Prompt
    from mcp.server.mcpserver.tools import Tool

    from .config import HirakuConfig

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])
_TOOL_MARKER = "__hiraku_tool__"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# No underscores: MCP names are "<workflow-id>_<name>", split at the first "_".
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_MAX_TOOL_NAME = 64
RESERVED_PARAMETERS = frozenset({"session_id", "instruction_id"})

HandlerKind = Literal["start", "continuation", "tool"]


@overload
def tool(function: _F, /) -> _F: ...


@overload
def tool(name: str | None = None, /) -> Callable[[_F], _F]: ...


def tool(target: Any = None, /) -> Any:
    """Register a workflow method as an MCP tool.

    Use ``@tool`` to name the tool after the method, or ``@tool("name")`` to
    choose the tool name separately, e.g. when the method name is taken by a
    `Workflow` attribute such as ``save``.

    The method's signature and docstring define the arguments and description.
    The tool named ``start`` creates sessions; agents call it as the tool named
    after the workflow, and users as the prompt of the same name. A method with
    one parameter annotated as a `Session` subclass is a reply, sent through the
    ``workflow_reply`` tool only while its session's pending instruction lists
    it. Any other method is an ordinary tool, ``<workflow-id>_<name>``, that does
    not touch session state.

    The decorated method stays directly callable from Python.

    Example:
        ```python
        @tool("save")
        def save_result(self, session: MySession, run_id: str): ...
        ```
    """

    def register(function: _F, name: str | None) -> _F:
        setattr(function, _TOOL_MARKER, name or getattr(function, "__name__", ""))
        return function

    if callable(target):
        return register(target, None)
    if target is not None and not isinstance(target, str):
        raise TypeError("tool() takes a tool name string")
    return lambda function: register(function, target)


@dataclass(frozen=True)
class Handler:
    """A registered workflow method and its derived argument model."""

    name: str
    method: str
    kind: HandlerKind
    function: Callable[..., Any]
    arguments: type[BaseModel]
    session_parameter: str | None
    session_model: type[Session] | None
    description: str


class Workflow(BaseRecord):
    """A workflow: its ``workflow.md`` record and its operations.

    Subclass it in ``hiraku/workflows/<workflow-id>/workflow.py``, export the
    class as ``WORKFLOW``, and decorate its operations with `tool`. An instance
    is the loaded ``workflow.md``: the front matter holds the workflow ``id``,
    which must match the directory name, and any settings the subclass declares
    as annotated fields; the Markdown body holds orientation and prompt
    sections.

    The base class alone loads any ``workflow.md`` without importing project
    code. The runtime loads each workflow once and gives every call a fresh copy
    bound to the project; changes to an instance are not persisted.

    Note:
        Class bodies follow Pydantic rules: annotated attributes are front-matter
        fields, constants need ``ClassVar``, and ``__init__`` is not overridden.
    """

    _parsed: ParsedMarkdown | None = PrivateAttr(default=None)
    _config: HirakuConfig | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def parse_sections(self) -> Self:
        self._parsed = parse_markdown(self.markdown, label=f"workflow {self.id!r}")
        return self

    def bind(self, config: HirakuConfig) -> Self:
        """Return a copy bound to a project."""
        bound = self.model_copy(deep=True)
        bound._config = config
        return bound

    def call(
        self, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run one of this workflow's tools, as the MCP server and the CLI do.

        ``start`` creates a session, a reply continues the pending instruction
        of a stored session, and any other tool runs without a session. The
        workflow must be bound to a project with `bind`.

        Args:
            tool: Tool name without the workflow prefix, e.g. ``"start"`` or
                ``"submit_run"``.
            arguments: JSON-compatible tool arguments. Replies also take
                ``instruction_id`` and, optionally, ``session_id``; without it,
                the reply goes to the one active session of this workflow.

        Returns:
            A JSON-compatible response whose ``status`` is ``accepted``,
            ``rejected``, ``not_ready``, ``stale``, or ``error`` (``ok`` for
            tools that do not use sessions).

        Raises:
            hiraku.errors.HirakuError: If the tool or session does not exist or
                the correlation arguments are malformed.
        """
        handler = handlers(type(self)).get(tool)
        if handler is None:
            raise WorkflowError(f"Unknown workflow tool {tool_name(self.id, tool)!r}")
        if arguments is not None and not isinstance(arguments, Mapping):
            raise ValidationError("Tool arguments must be a JSON object")
        values = dict(arguments or {})
        if handler.kind == "continuation":
            return _reply(self, handler, values)
        model = self._start_model() if handler.kind == "start" else handler.arguments
        validated, failure = _validate(model, values)
        if failure is None:
            keywords = (
                self._start_keywords(validated)
                if handler.kind == "start"
                else _keywords(validated)
            )
            response, failure = _invoke(self, handler, keywords)
        if failure is not None:
            return failure
        if handler.kind == "tool":
            return {"status": "ok", "result": to_jsonable_python(response)}
        try:
            session = _checked_response(self, response)
        except WorkflowError as exc:
            return {"status": "error", "message": str(exc)}
        now = datetime.now(UTC)
        session.id = new_session_id(self.id)
        session.workflow = self.id
        session.created_at = now
        call = HistoryEntry(
            instruction_id=0,
            reply="start",
            arguments=validated.model_dump(mode="json"),
            at=now,
        )
        session.advance(response, _reply_specs(self), call)
        session.save(config=self.config)
        return {
            "status": "accepted",
            "guide": GUIDE,
            "orientation": self.orientation,
            **session.response,
        }

    @classmethod
    def tools(
        cls,
        project: Callable[[], HirakuConfig],
        workflows: Mapping[str, Workflow] | None = None,
    ) -> list[Tool]:
        """Return the agent tools for running workflows, e.g. over MCP.

        Each workflow is started by a tool named after it, which takes the
        workflow's start arguments. Every reply of every workflow goes through
        one ``workflow_reply`` tool; instructions describe their replies'
        arguments. Other workflow tools are named ``<workflow-id>_<name>``.

        Args:
            project: Returns the project configuration as currently saved.
            workflows: Loaded workflows by ID; loaded from the project if
                omitted.
        """
        from mcp.server.mcpserver.tools import Tool
        from mcp.types import ToolAnnotations

        loaded = load_workflows(project()) if workflows is None else workflows

        def workflow_list() -> list[dict[str, Any]]:
            return list_workflows(loaded)

        def workflow_reply(
            reply: Annotated[
                str, Field(description="One of the replies the instruction lists.")
            ],
            instruction_id: Annotated[
                int, Field(description="instruction_id of the pending instruction.")
            ],
            arguments: Annotated[
                dict[str, Any] | None,
                Field(description="The reply's arguments, as its schema describes."),
            ] = None,
            session_id: Annotated[
                str | None,
                Field(
                    description="session_id of the instruction; may be omitted "
                    "when only one session is active."
                ),
            ] = None,
        ) -> dict[str, Any]:
            return _workflow_reply(
                loaded, project(), reply, instruction_id, arguments, session_id
            )

        tools = [
            Tool.from_function(
                workflow_list,
                description="List the project's workflows. Start one with the tool "
                "named after it.",
                annotations=ToolAnnotations(read_only_hint=True),
            ),
            Tool.from_function(
                workflow_reply,
                description="Reply to the pending instruction of a workflow "
                "session with one of the replies it lists.",
                annotations=ToolAnnotations(read_only_hint=False),
            ),
        ]
        for workflow_id, workflow in sorted(loaded.items()):
            for handler in sorted(
                handlers(type(workflow)).values(), key=lambda item: item.name
            ):
                if handler.kind == "continuation":
                    continue
                name = (
                    workflow_id
                    if handler.kind == "start"
                    else tool_name(workflow_id, handler.name)
                )
                tools.append(
                    Tool.from_function(
                        _tool_function(workflow, handler, project),
                        name=name,
                        description=_tool_description(workflow, handler),
                    )
                )
        return tools

    @classmethod
    def prompts(
        cls,
        project: Callable[[], HirakuConfig],
        workflows: Mapping[str, Workflow] | None = None,
    ) -> list[Prompt]:
        """Return one prompt per workflow that starts it for the user.

        Getting the prompt starts a session and returns its first instruction
        as the user message. Prompt arguments are strings and are converted to
        the workflow's start arguments.

        Args:
            project: Returns the project configuration as currently saved.
            workflows: Loaded workflows by ID; loaded from the project if
                omitted.
        """
        from mcp.server.mcpserver.prompts import Prompt

        loaded = load_workflows(project()) if workflows is None else workflows
        return [
            Prompt.from_function(
                _prompt_function(workflow, project),
                name=workflow_id,
                description=summary(workflow_description(workflow))
                or f"Start the {workflow_id} workflow.",
            )
            for workflow_id, workflow in sorted(loaded.items())
        ]

    def _start_model(self) -> type[BaseModel]:
        """Return the model of the start arguments."""
        return handlers(type(self))["start"].arguments

    def _start_keywords(self, validated: BaseModel) -> dict[str, Any]:
        """Return the keyword arguments of ``start`` for validated arguments."""
        return _keywords(validated)

    @property
    def config(self) -> HirakuConfig:
        """Return the project configuration of a bound workflow."""
        if self._config is None:
            raise WorkflowError(f"Workflow {self.id!r} is not bound to a project")
        return self._config

    @property
    def text(self) -> str:
        """Return the Markdown body without front matter."""
        return self.markdown.strip()

    @property
    def anchors(self) -> list[str]:
        """Return all section anchors in document order."""
        return list(self._sections().sections)

    @property
    def orientation(self) -> str:
        """Return the introduction before the first second-level heading."""
        parsed = self._sections()
        return "".join(parsed.lines[: parsed.orientation_end]).strip()

    def section(self, anchor: str) -> str:
        """Return a section of the body, including its subsections.

        Raises:
            hiraku.errors.WorkflowError: If the anchor does not exist.
        """
        parsed = self._sections()
        try:
            section = parsed.sections[anchor]
        except KeyError as exc:
            available = ", ".join(f"#{name}" for name in parsed.sections) or "none"
            raise WorkflowError(
                f"Prompt section #{anchor} not found in workflow {self.id!r}; "
                f"available sections: {available}"
            ) from exc
        return "".join(parsed.lines[section.start : section.end]).strip()

    def _sections(self) -> ParsedMarkdown:
        if self._parsed is None:
            self._parsed = parse_markdown(self.markdown, label=f"workflow {self.id!r}")
        return self._parsed

    def instruction(
        self,
        session: Session,
        text: str,
        *,
        replies: Sequence[Callable[..., Any]],
        context: Any = None,
        requires_user_response: bool = False,
    ) -> Instruction:
        """Build the next instruction.

        Args:
            session: Session the instruction belongs to.
            text: Actionable guidance for the harness.
            replies: Continuation handlers of this workflow, e.g.
                ``[self.select, self.stop]``.
            context: Concise JSON-compatible data.
            requires_user_response: Whether a user answer is required before
                replying.

        Raises:
            hiraku.errors.WorkflowError: If a reply is not a continuation handler
                of this workflow or the context is not JSON-compatible.
        """
        if not isinstance(session, Session):
            raise WorkflowError("Instructions require a Session instance")
        if not text or not text.strip():
            raise WorkflowError("Instruction text must not be empty")
        if not replies:
            raise WorkflowError("An instruction must offer at least one reply")
        names = tuple(self._reply_name(reply) for reply in replies)
        if len(names) != len(set(names)):
            raise WorkflowError("An instruction lists the same reply more than once")
        return Instruction(
            session=session,
            text=text.strip(),
            replies=names,
            context=_ensure_json(context, "Instruction context"),
            requires_user_response=bool(requires_user_response),
        )

    def completion(self, session: Session, result: Any = None) -> Completion:
        """Build a terminal response with a JSON-compatible result."""
        if not isinstance(session, Session):
            raise WorkflowError("Completion requires a Session instance")
        return Completion(
            session=session, result=_ensure_json(result, "Completion result")
        )

    def _reply_name(self, reply: Callable[..., Any]) -> str:
        function = getattr(reply, "__func__", None)
        if getattr(reply, "__self__", None) is not self or function is None:
            raise WorkflowError(
                "Replies must be bound continuation methods of this workflow, "
                "e.g. self.select"
            )
        for handler in handlers(type(self)).values():
            if handler.function is function:
                if handler.kind != "continuation":
                    raise WorkflowError(
                        f"Reply {handler.name!r} is not a continuation handler; "
                        "it needs a Session parameter"
                    )
                return handler.name
        raise WorkflowError(
            f"Reply {getattr(function, '__name__', reply)!r} is not a registered "
            "@tool of this workflow"
        )


_ARGUMENT_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


class Argument(BaseModel):
    """A start argument declared in the front matter of a Markdown workflow.

    Attributes:
        type: ``string``, ``integer``, ``number``, or ``boolean``.
        description: What the argument means, shown to the user and the agent.
        required: Whether the argument must be given.
        default: Value used when the argument is not given.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str | None = None
    required: bool = False
    default: str | int | float | bool | None = None


class PromptWorkflow(Workflow):
    """Send the whole workflow.md as one instruction and finish on the reply.

    Used for workflow directories that contain only ``workflow.md``. Its front
    matter may declare start ``arguments``, which fill ``{{name}}``
    placeholders in the body. Hiraku checks nothing about the work itself; the
    agent follows the prompt and may report a summary when it is done.

    Example:
        ```markdown
        ---
        id: loop
        arguments:
          iterations: {type: integer, default: 5, description: Experiments to run}
        ---
        Run {{iterations}} experiments.
        ```
    """

    arguments: dict[str, Argument] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_placeholders(self) -> Self:
        undeclared = sorted(placeholders(self.markdown) - set(self.arguments))
        if undeclared:
            raise ValidationError(
                f"Workflow {self.id!r} uses placeholders without declared "
                f"arguments: {', '.join(undeclared)}"
            )
        return self

    @property
    def orientation(self) -> str:
        """Return nothing: the whole body is the first instruction."""
        return ""

    @tool
    def start(self, arguments: dict[str, Any] | None = None) -> Instruction:
        """Start the workflow; its whole prompt arrives as one instruction."""
        values = dict(arguments or {})
        return self.instruction(
            Session(),
            render(self.text, values),
            context=values or None,
            replies=[self.complete],
        )

    @tool
    def complete(
        self,
        session: Session,
        summary: Annotated[
            str, Field(description="What was done, what resulted, what remains open.")
        ],
    ) -> Completion:
        """Report that the work is finished, with a summary for the user."""
        if not summary.strip():
            raise SubmissionRejected("Summarize what was done")
        return self.completion(session, {"summary": summary.strip()})

    def _start_model(self) -> type[BaseModel]:
        fields: dict[str, Any] = {}
        for name, argument in self.arguments.items():
            kind = _ARGUMENT_TYPES[argument.type]
            info = Field(description=argument.description)
            if argument.required:
                fields[name] = (Annotated[kind, info], ...)  # ty: ignore[invalid-type-form]
            else:
                optional = Annotated[kind | None, info]  # ty: ignore[invalid-type-form]
                fields[name] = (optional, argument.default)
        return create_model(
            f"{self.id}_arguments", __config__=ConfigDict(extra="forbid"), **fields
        )

    def _start_keywords(self, validated: BaseModel) -> dict[str, Any]:
        return {"arguments": validated.model_dump(mode="json")}


def discover_workflows(config: HirakuConfig) -> list[str]:
    """Return the IDs of workflow directories, without importing them.

    A workflow directory contains ``workflow.py``, ``workflow.md``, or both.
    """
    root = config.workflows_dir
    if not root.is_dir():
        return []
    return [
        path.name
        for path in sorted(root.iterdir())
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and any((path / name).is_file() for name in ("workflow.py", "workflow.md"))
    ]


def load_workflow(workflow_id: str, *, config: HirakuConfig | None = None) -> Workflow:
    """Load a project workflow: its class and its ``workflow.md`` record.

    Imports ``workflow.py`` when present; it must export ``WORKFLOW``, a
    `Workflow` subclass. A directory with only ``workflow.md`` loads as a
    `PromptWorkflow`. Each call imports the code again.

    Args:
        workflow_id: Workflow directory name.
        config: Project configuration; discovered when omitted.

    Raises:
        hiraku.errors.WorkflowError: If the ID is invalid, the code cannot be
            imported, or its tools are invalid.
        hiraku.errors.RecordNotFoundError: If the workflow has no workflow.md.
        hiraku.errors.ValidationError: If the record is invalid or its ID does
            not match the workflow directory.

    Note:
        Importing a workflow executes trusted project Python code.
    """
    from .config import HirakuConfig

    selected = config or HirakuConfig.discover()
    if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id):
        raise WorkflowError(
            f"Invalid workflow ID {workflow_id!r}: use letters, digits and '-', "
            "starting with a letter or digit"
        )
    directory = selected.workflows_dir / workflow_id
    if not directory.is_dir():
        available = ", ".join(discover_workflows(selected)) or "none"
        raise WorkflowError(
            f"Unknown workflow {workflow_id!r}; available workflows: {available}"
        )
    cls = (
        _import_workflow(directory)
        if (directory / "workflow.py").is_file()
        else PromptWorkflow
    )
    for name in handlers(cls):
        if len(f"{workflow_id}_{name}") > _MAX_TOOL_NAME:
            raise WorkflowError(
                f"Name {workflow_id}_{name!r} exceeds {_MAX_TOOL_NAME} characters"
            )
    path = directory / "workflow.md"
    try:
        workflow = cls.load(path)
    except PydanticValidationError as exc:
        raise ValidationError(f"Invalid workflow settings in {path}: {exc}") from exc
    if workflow.id != workflow_id:
        raise ValidationError(
            f"{path} declares id {workflow.id!r}; it must match the workflow "
            f"directory {workflow_id!r}"
        )
    return workflow


def load_workflows(config: HirakuConfig) -> dict[str, Workflow]:
    """Load every project workflow that loads; log and skip the others."""
    loaded = {}
    for workflow_id in discover_workflows(config):
        try:
            loaded[workflow_id] = load_workflow(workflow_id, config=config)
        except Exception as exc:
            logger.warning("Workflow %s is unavailable: %s", workflow_id, exc)
    return loaded


def list_workflows(workflows: Mapping[str, Workflow]) -> list[dict[str, Any]]:
    """Describe loaded workflows: ID and description."""
    return [
        {"id": workflow_id, "description": workflow_description(workflow)}
        for workflow_id, workflow in sorted(workflows.items())
    ]


def workflow_description(workflow: Workflow) -> str:
    """Return the ``description`` setting of a workflow, or its class docstring."""
    described = workflow.to_dict().get("description")
    return str(described or type(workflow).__doc__ or "").strip()


def tool_name(workflow_id: str, name: str) -> str:
    """Return the full name of a workflow tool, ``<workflow-id>_<name>``."""
    return f"{workflow_id}_{name}"


def split_tool_name(name: str) -> tuple[str, str]:
    """Split a full tool name into workflow ID and tool name.

    Raises:
        hiraku.errors.WorkflowError: If the name has no workflow prefix.
    """
    workflow_id, separator, tool = name.partition("_")
    if not separator or not workflow_id or not tool:
        raise WorkflowError(f"Unknown workflow tool {name!r}")
    return workflow_id, tool


def _import_workflow(directory: Path) -> type[Workflow]:
    # Each workflow directory becomes a package below a project-specific root
    # package, so relative imports work without changing sys.path.
    digest = hashlib.sha256(str(directory.parent.resolve()).encode()).hexdigest()
    package = f"_hiraku_workflows_{digest[:16]}.{directory.name}"
    root = package.split(".")[0]
    for name in [
        key for key in sys.modules if key == package or key.startswith(f"{package}.")
    ]:
        del sys.modules[name]
    for name, location in ((root, directory.parent), (package, directory)):
        if name not in sys.modules:
            spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
            spec.submodule_search_locations = [str(location)]
            sys.modules[name] = importlib.util.module_from_spec(spec)
    path = directory / "workflow.py"
    spec = importlib.util.spec_from_file_location(f"{package}.workflow", path)
    if spec is None or spec.loader is None:
        raise WorkflowError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        raise WorkflowError(
            f"Cannot import {path}: {type(exc).__name__}: {exc}"
        ) from exc
    cls = getattr(module, "WORKFLOW", None)
    if not (isinstance(cls, type) and issubclass(cls, Workflow)):
        raise WorkflowError(
            f"{path} must export WORKFLOW, a subclass of hiraku.workflow.Workflow"
        )
    return cls


def _ensure_json(value: Any, label: str) -> Any:
    try:
        converted = to_jsonable_python(value)
        json.dumps(converted, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f"{label} must be JSON-compatible: {exc}") from exc
    return converted


@cache
def handlers(cls: type[Workflow]) -> dict[str, Handler]:
    """Collect and validate the `tool` methods of a workflow class.

    Raises:
        hiraku.errors.WorkflowError: If a signature is unsupported, continuation
            handlers disagree on the session model, or ``start`` is missing.
    """
    if not (isinstance(cls, type) and issubclass(cls, Workflow)):
        raise WorkflowError(f"{cls!r} is not a Workflow subclass")
    collected = {
        name: _handler(cls, name, method, function)
        for name, method, function in _tool_methods(cls)
    }
    if "start" not in collected:
        raise WorkflowError(f"Workflow {cls.__name__} must define a @tool start")
    models = {
        handler.session_model
        for handler in collected.values()
        if handler.session_model is not None
    }
    if len(models) > 1:
        names = ", ".join(sorted(model.__name__ for model in models if model))
        raise WorkflowError(
            f"Continuation handlers of {cls.__name__} use different session "
            f"models: {names}"
        )
    return collected


def _tool_methods(cls: type[Workflow]) -> list[tuple[str, str, Callable[..., Any]]]:
    """Return ``(name, method, function)`` for the `tool` methods of a class."""
    result: list[tuple[str, str, Callable[..., Any]]] = []
    seen: dict[str, str] = {}
    for method in sorted(dir(cls)):
        value = inspect.getattr_static(cls, method, None)
        name = getattr(value, _TOOL_MARKER, None)
        if not isinstance(name, str):
            continue
        if hasattr(Workflow, method):
            raise WorkflowError(
                f"@tool method {cls.__name__}.{method} shadows a Workflow "
                f'attribute; rename the method and keep the name with @tool("'
                f'{name}")'
            )
        if not _TOOL_NAME.fullmatch(name):
            raise WorkflowError(
                f"Invalid tool name {name!r} on {cls.__name__}.{method}: use "
                "letters, digits, '-' and '_'"
            )
        if name in seen:
            raise WorkflowError(
                f"Tool name {name!r} is used by both "
                f"{cls.__name__}.{seen[name]} and {cls.__name__}.{method}"
            )
        if not inspect.isfunction(value):
            raise WorkflowError(f"@tool {cls.__name__}.{method} must be a method")
        seen[name] = method
        result.append((name, method, value))
    return result


def session_model(cls: type[Workflow]) -> type[Session] | None:
    """Return the session model shared by a workflow's continuation handlers."""
    for handler in handlers(cls).values():
        if handler.session_model is not None:
            return handler.session_model
    return None


def _handler(
    cls: type[Workflow], name: str, method: str, function: Callable[..., Any]
) -> Handler:
    label = f"{cls.__name__}.{method}"
    if inspect.iscoroutinefunction(function):
        raise WorkflowError(f"@tool {label} must be synchronous")
    try:
        signature = inspect.signature(function)
        hints = typing.get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as exc:
        raise WorkflowError(f"Cannot resolve annotations of {label}: {exc}") from exc
    parameters = list(signature.parameters.values())
    if not parameters or parameters[0].name != "self":
        raise WorkflowError(f"@tool {label} must be an instance method")
    fields: dict[str, Any] = {}
    session_parameter: str | None = None
    model: type[Session] | None = None
    for parameter in parameters[1:]:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise WorkflowError(
                f"@tool {label} cannot use *args, **kwargs, or positional-only "
                f"parameter {parameter.name!r}"
            )
        if parameter.name in RESERVED_PARAMETERS:
            raise WorkflowError(
                f"@tool {label} cannot declare reserved parameter {parameter.name!r}"
            )
        if parameter.name not in hints:
            raise WorkflowError(
                f"@tool {label} parameter {parameter.name!r} needs a type annotation"
            )
        annotation = hints[parameter.name]
        if isinstance(annotation, type) and issubclass(annotation, Session):
            if session_parameter is not None:
                raise WorkflowError(f"@tool {label} declares more than one session")
            if parameter.default is not inspect.Parameter.empty:
                raise WorkflowError(f"@tool {label} session cannot have a default")
            session_parameter, model = parameter.name, annotation
            continue
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (annotation, default)
    kind: HandlerKind
    if name == "start":
        if session_parameter is not None:
            raise WorkflowError(f"@tool {label} creates sessions; it cannot take one")
        kind = "start"
    elif session_parameter is not None:
        kind = "continuation"
    else:
        kind = "tool"
    try:
        arguments = create_model(
            f"{cls.__name__}_{method}_arguments",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        arguments.model_json_schema()
    except Exception as exc:
        raise WorkflowError(
            f"Cannot derive an argument schema for {label}: {exc}"
        ) from exc
    return Handler(
        name=name,
        method=method,
        kind=kind,
        function=function,
        arguments=arguments,
        session_parameter=session_parameter,
        session_model=model,
        description=inspect.getdoc(function) or "",
    )


# Running tools -------------------------------------------------------------------


def _reply(
    workflow: Workflow, handler: Handler, values: dict[str, Any]
) -> dict[str, Any]:
    instruction_id = values.pop("instruction_id", None)
    if isinstance(instruction_id, bool) or not isinstance(instruction_id, int):
        raise ValidationError("instruction_id must be an integer")
    session_id = values.pop("session_id", None)
    if session_id is None:
        session_id, failure = _active_session(workflow)
        if failure is not None:
            return failure
    validate_session_id(session_id)
    try:
        with locked(session_id, config=workflow.config):
            return _reply_locked(workflow, handler, session_id, instruction_id, values)
    except Timeout:
        return {
            "status": "rejected",
            "message": f"Session {session_id!r} is busy with another call; retry "
            "after it finishes",
        }


def _reply_locked(
    workflow: Workflow,
    handler: Handler,
    session_id: str,
    instruction_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    stored = Session.load(session_id, config=workflow.config)
    if stored.workflow != workflow.id:
        return stored.outcome(
            "rejected",
            f"Session {session_id!r} belongs to workflow {stored.workflow!r}, "
            f"not {workflow.id!r}",
        )
    validated, failure = _validate(handler.arguments, values)
    if failure is not None:
        return {**failure, **stored.response}
    arguments = validated.model_dump(mode="json")
    early = stored.check(handler.name, instruction_id, arguments)
    if early is not None:
        return early
    assert handler.session_model is not None
    assert handler.session_parameter is not None
    try:
        session = handler.session_model.load(session_id, config=workflow.config)
    except ValidationError as exc:
        return stored.outcome("rejected", _incompatible(exc))
    keywords = _keywords(validated)
    keywords[handler.session_parameter] = session
    response, failure = _invoke(workflow, handler, keywords)
    if failure is not None:
        return {**failure, **stored.response}
    try:
        result = _checked_response(workflow, response)
    except WorkflowError as exc:
        return stored.outcome("error", str(exc))
    # The runtime owns the base fields; discard any changes the tool made.
    for name in Session.model_fields:
        setattr(result, name, getattr(stored, name))
    call = HistoryEntry(
        instruction_id=instruction_id,
        reply=handler.name,
        arguments=arguments,
        at=datetime.now(UTC),
    )
    result.advance(response, _reply_specs(workflow), call)
    result.save(config=workflow.config)
    return {"status": "accepted", **result.response}


def _active_session(workflow: Workflow) -> tuple[str, dict[str, Any] | None]:
    active = [
        item["session_id"]
        for item in Session.list_all(config=workflow.config)
        if item.get("workflow") == workflow.id and item.get("status") == "active"
    ]
    if len(active) == 1:
        return active[0], None
    if active:
        message = (
            f"Several {workflow.id} sessions are active ({', '.join(active)}); "
            "pass session_id"
        )
    else:
        message = (
            f"No active {workflow.id} session; start one with the {workflow.id} tool"
        )
    return "", {"status": "rejected", "message": message}


def _validate(
    model: type[BaseModel], values: dict[str, Any]
) -> tuple[Any, dict[str, Any] | None]:
    """Return the validated arguments, or a failure status."""
    try:
        return model.model_validate(values), None
    except PydanticValidationError as exc:
        return None, {"status": "rejected", "message": validation_message(exc)}


def _invoke(
    workflow: Workflow, handler: Handler, keywords: dict[str, Any]
) -> tuple[Any, dict[str, Any] | None]:
    """Call a tool; return its result, or a failure status."""
    try:
        return handler.function(workflow, **keywords), None
    except NotReady as exc:
        return None, {"status": "not_ready", "message": str(exc)}
    except (HirakuError, PydanticValidationError) as exc:
        return None, {"status": "rejected", "message": str(exc)}
    except Exception as exc:
        logger.exception("Workflow %s tool %s failed", workflow.id, handler.name)
        return None, {
            "status": "error",
            "message": (
                f"{tool_name(workflow.id, handler.name)} failed unexpectedly with "
                f"{type(exc).__name__}: {exc}. No session progress was saved."
            ),
        }


def _checked_response(workflow: Workflow, response: Any) -> Session:
    if not isinstance(response, Instruction | Completion):
        raise WorkflowError(
            f"Workflow {workflow.id!r} returned {type(response).__name__}; tools "
            "that use sessions must return self.instruction(...) or "
            "self.completion(...)"
        )
    session = response.session
    model = session_model(type(workflow))
    if model is not None and type(session) is not model:
        raise WorkflowError(
            f"Workflow {workflow.id!r} returned a {type(session).__name__} session; "
            f"expected {model.__name__}"
        )
    try:
        type(session).model_validate(session.model_dump(mode="json"))
    except PydanticValidationError as exc:
        raise WorkflowError(
            f"Session of workflow {workflow.id!r} does not round-trip through JSON: "
            f"{validation_message(exc)}"
        ) from exc
    return session


def compatibility_problem(workflow: Workflow, session: Session) -> str | None:
    """Check a stored session against the loaded workflow code.

    Returns:
        A description of the incompatibility, or ``None`` if the session can
        continue. Sessions continue with the currently loaded code; authors are
        responsible for keeping it compatible with existing sessions.
    """
    registered = handlers(type(workflow))
    missing = [
        name
        for name in session.replies
        if name not in registered or registered[name].kind != "continuation"
    ]
    if missing:
        return (
            "The loaded workflow no longer defines the pending replies "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    model = session_model(type(workflow))
    if model is None:
        return "The loaded workflow defines no replies."
    try:
        model.load(session.id, config=session.config)
    except ValidationError as exc:
        return _incompatible(exc)
    return None


def _incompatible(exc: ValidationError) -> str:
    return (
        f"The stored session no longer matches the loaded workflow: {exc}. "
        "Inspect the session and start a new one."
    )


def _reply_specs(workflow: Workflow) -> dict[str, dict[str, Any]]:
    """Return the description and argument schema of each reply, by name."""
    return {
        name: {
            "description": summary(handler.description),
            "arguments": _compact_schema(handler.arguments),
        }
        for name, handler in handlers(type(workflow)).items()
        if handler.kind == "continuation"
    }


def _compact_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a model's JSON schema without titles, for instructions."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip(item)
                for key, item in value.items()
                if not (key == "title" and isinstance(item, str))
                and key != "additionalProperties"
            }
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    schema = strip(model.model_json_schema())
    schema.pop("type", None)
    schema.setdefault("properties", {})
    return schema


def _keywords(validated: BaseModel) -> dict[str, Any]:
    return {name: getattr(validated, name) for name in type(validated).model_fields}


def summary(text: str) -> str:
    """Return the first paragraph of a description on one line."""
    return text.strip().split("\n\n", 1)[0].replace("\n", " ") if text else ""


def validation_message(exc: PydanticValidationError) -> str:
    """Return a compact message for a Pydantic validation error."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "input"
        parts.append(f"{location}: {error.get('msg')}")
    return "; ".join(parts)


__all__ = [
    "Completion",
    "Instruction",
    "NotReady",
    "PromptWorkflow",
    "Response",
    "Session",
    "SubmissionRejected",
    "Workflow",
    "discover_workflows",
    "list_workflows",
    "load_workflow",
    "load_workflows",
    "tool",
]


def _tool_function(
    workflow: Workflow, handler: Handler, project: Callable[[], HirakuConfig]
) -> Callable[..., Any]:
    """Return a function whose signature is a start or plain tool's schema."""
    model = workflow._start_model() if handler.kind == "start" else handler.arguments

    def run(**arguments: Any) -> dict[str, Any]:
        return workflow.bind(project()).call(handler.name, arguments)

    parameters = []
    for name, field in model.model_fields.items():
        default = (
            inspect.Parameter.empty
            if field.is_required()
            else field.get_default(call_default_factory=True)
        )
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=Annotated[
                    (
                        field.annotation,
                        *field.metadata,
                        Field(description=field.description),
                    )
                ],
            )
        )
    return _with_signature(run, workflow.id, handler.name, parameters)


def _prompt_function(
    workflow: Workflow, project: Callable[[], HirakuConfig]
) -> Callable[..., str]:
    """Return a function that starts a session and renders its instruction.

    Prompt arguments are strings; the start arguments' model converts them.
    """

    def run(**arguments: Any) -> str:
        values = {key: value for key, value in arguments.items() if value is not None}
        response = workflow.bind(project()).call("start", values)
        if response.get("status") != "accepted":
            # The SDK hides exception messages from prompts, so explain in text.
            return (
                f"The Hiraku workflow `{workflow.id}` did not start: "
                f"{response.get('message')}. Tell the user and ask for corrected "
                "arguments."
            )
        return _prompt_text(response)

    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if field.is_required() else None,
            annotation=Annotated[
                str if field.is_required() else str | None,  # ty: ignore[invalid-type-form]
                Field(description=field.description),
            ],
        )
        for name, field in workflow._start_model().model_fields.items()
    ]
    return _with_signature(run, workflow.id, "prompt", parameters)


def _with_signature(
    function: Callable[..., Any],
    workflow_id: str,
    name: str,
    parameters: list[inspect.Parameter],
) -> Callable[..., Any]:
    function.__name__ = tool_name(workflow_id, name).replace("-", "_")  # ty: ignore[unresolved-attribute]
    function.__signature__ = inspect.Signature(parameters)  # ty: ignore[unresolved-attribute]
    # Pydantic's validate_call, used for prompts, reads the annotations.
    function.__annotations__ = {item.name: item.annotation for item in parameters}
    return function


def _prompt_text(response: Mapping[str, Any]) -> str:
    """Render a start response as a user message for the agent."""
    instruction = response["instruction"]
    parts = []
    orientation = response.get("orientation") or ""
    if orientation and not instruction["text"].startswith(orientation):
        parts.append(orientation)
    parts.append(instruction["text"])
    if instruction.get("context") is not None:
        context = json.dumps(instruction["context"], indent=1, ensure_ascii=False)
        parts.append(f"Context:\n```json\n{context}\n```")
    replies = json.dumps(instruction["replies"], indent=1, ensure_ascii=False)
    parts.append(
        f"---\nThis is Hiraku workflow session `{response['session_id']}`, "
        f"instruction {response['instruction_id']}. Reply with the workflow_reply "
        f"tool, passing session_id `{response['session_id']}`, instruction_id "
        f"{response['instruction_id']}, one of these replies, and its "
        f"arguments:\n```json\n{replies}\n```\n\n{response['guide']}"
    )
    return "\n\n".join(parts)


def _workflow_reply(
    workflows: Mapping[str, Workflow],
    config: HirakuConfig,
    reply: str,
    instruction_id: int,
    arguments: Mapping[str, Any] | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Send a reply to the pending instruction of a session of any workflow."""
    if session_id is None:
        active = [
            item["session_id"]
            for item in Session.list_all(config=config)
            if item.get("status") == "active"
        ]
        if len(active) != 1:
            return {
                "status": "rejected",
                "message": (
                    f"Several sessions are active ({', '.join(active)}); pass "
                    "session_id"
                    if active
                    else "No session is active; start a workflow first"
                ),
            }
        session_id = active[0]
    session = Session.load(session_id, config=config)
    workflow = workflows.get(session.workflow)
    if workflow is None:
        return session.outcome(
            "rejected",
            f"Workflow {session.workflow!r} is unavailable; the session cannot "
            "continue until it loads.",
        )
    handler = handlers(type(workflow)).get(reply)
    if handler is None or handler.kind != "continuation":
        return session.outcome(
            "rejected",
            f"{reply!r} is not a reply of workflow {workflow.id!r}; the pending "
            f"instruction accepts: {', '.join(session.replies) or 'none'}",
        )
    values = {
        **dict(arguments or {}),
        "instruction_id": instruction_id,
        "session_id": session_id,
    }
    return workflow.bind(config).call(reply, values)


def _tool_description(workflow: Workflow, handler: Handler) -> str:
    description = handler.description or f"{handler.name} of workflow {workflow.id}"
    if handler.kind == "start":
        prefix = f"Start a {workflow.id} workflow session."
        if text := summary(workflow_description(workflow)):
            prefix += f" {text}"
        return f"{prefix}\n\n{description}"
    return description
