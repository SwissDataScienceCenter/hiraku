"""Workflow records, sessions, tools, and responses."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
import re
import sys
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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

from ..base import BaseRecord
from ..errors import SubmissionRejected, ValidationError, WorkflowError
from .prompts import ParsedMarkdown, parse_markdown

if TYPE_CHECKING:
    from ..config import HirakuConfig

_F = TypeVar("_F", bound=Callable[..., Any])
_TOOL_MARKER = "__hiraku_tool__"
_RESOURCE_MARKER = "__hiraku_resource__"
_MIME_MARKER = "__hiraku_resource_mime_type__"
_PROMPT_MARKER = "__hiraku_prompt__"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# No underscores: MCP names are "<workflow-id>_<name>", split at the first "_".
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_MAX_TOOL_NAME = 64
RESERVED_PARAMETERS = frozenset({"session_id", "instruction_id"})

HandlerKind = Literal["start", "continuation", "tool"]


class Session(BaseModel):
    """Base class for the persisted data of one workflow invocation.

    Subclasses declare typed fields for resolved arguments, record references,
    counters, and continuation data. Sessions are stored as JSON, so fields must
    be JSON-serializable.

    Note:
        ``session_id`` is assigned by the runtime when the session is first
        saved; it is ``None`` inside ``start``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    _session_id: str | None = PrivateAttr(default=None)

    @property
    def session_id(self) -> str | None:
        """Return the persisted session ID, if the session has been saved."""
        return self._session_id


@overload
def tool(function: _F, /) -> _F: ...


@overload
def tool(name: str | None = None, /) -> Callable[[_F], _F]: ...


def tool(target: Any = None, /) -> Any:
    """Register a workflow method as an MCP tool.

    Use ``@tool`` to name the tool after the method, or ``@tool("name")`` to
    choose the tool name separately, e.g. when the method name is taken by a
    `Workflow` attribute such as ``save``. The MCP tool is called
    ``<workflow-id>_<name>``.

    The method's signature and docstring define the tool schema and description.
    The tool named ``start`` creates sessions. A method with one parameter
    annotated as a `Session` subclass is a continuation handler, invoked only as
    a reply to its session's pending instruction. Any other method is an
    ordinary tool that does not touch session state.

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


def resource(target: Any = None, /, *, mime_type: str = "text/markdown") -> Any:
    """Expose a workflow method as an MCP resource.

    The resource is ``hiraku://workflows/<workflow-id>/<name>``, where the name
    is the method name unless given, as in ``@resource("name")``; its text is
    the method's return value. The method takes no parameters besides ``self`` and
    is called with a fresh workflow instance when a client reads it. Resources
    of a workflow are listed only while the workflow is active.

    Example:
        ```python
        @resource
        def protocol(self) -> str:
            return self.section("protocol")
        ```
    """

    def register(function: _F, name: str | None) -> _F:
        setattr(function, _RESOURCE_MARKER, name or getattr(function, "__name__", ""))
        setattr(function, _MIME_MARKER, mime_type)
        return function

    if callable(target):
        return register(target, None)
    return lambda function: register(function, target)


def prompt(target: Any = None, /) -> Any:
    """Expose a workflow method as an MCP prompt named ``<workflow-id>_<name>``.

    The name is the method name unless given, as in ``@prompt("name")``.

    Clients may show MCP prompts as slash commands. The method's ``str``
    parameters become prompt arguments, and its returned text becomes the
    prompt's user message. It does not start or advance sessions.

    Example:
        ```python
        @prompt
        def resume(self, hint: str = "") -> str:
            return "Find the latest session with session_list and resume it."
        ```
    """

    def register(function: _F, name: str | None) -> _F:
        setattr(function, _PROMPT_MARKER, name or getattr(function, "__name__", ""))
        return function

    if callable(target):
        return register(target, None)
    return lambda function: register(function, target)


@dataclass(frozen=True)
class Instruction:
    """The next request to the harness, returned by a workflow handler.

    Attributes:
        session: Session whose state accompanies the instruction.
        text: Actionable guidance, usually taken from ``workflow.md``.
        replies: Names of the continuation handlers that may answer it.
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


@dataclass(frozen=True)
class WorkflowResource:
    """A workflow method exposed as an MCP resource."""

    name: str
    method: str
    function: Callable[..., Any]
    description: str
    mime_type: str


@dataclass(frozen=True)
class WorkflowPrompt:
    """A workflow method exposed as an MCP prompt."""

    name: str
    method: str
    function: Callable[..., Any]
    description: str
    arguments: type[BaseModel]


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


class PromptSession(Session):
    """Session of a Markdown-only workflow."""

    request: str | None = None
    summary: str | None = None


class PromptWorkflow(Workflow):
    """Send the whole workflow.md as one instruction and finish on the reply.

    Used for workflow directories that contain only ``workflow.md``. Hiraku
    checks nothing about the work itself; the agent follows the prompt and
    reports when it is done.
    """

    @tool
    def start(
        self,
        request: Annotated[
            str | None,
            Field(description="The user's request, goals, or limits for this run."),
        ] = None,
    ) -> Instruction:
        """Start the workflow; its whole prompt arrives as one instruction."""
        session = PromptSession(request=request)
        return self.instruction(
            session,
            self.text,
            context={"request": request} if request else None,
            replies=[self.complete],
        )

    @tool
    def complete(
        self,
        session: PromptSession,
        summary: Annotated[
            str, Field(description="What was done, what resulted, what remains open.")
        ],
    ) -> Completion:
        """Report that the work is finished, with a summary for the user."""
        if not summary.strip():
            raise SubmissionRejected("Summarize what was done")
        session.summary = summary.strip()
        return self.completion(session, {"summary": session.summary})


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
    from ..config import HirakuConfig

    selected = config or HirakuConfig.discover()
    if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id):
        raise WorkflowError(
            f"Invalid workflow ID {workflow_id!r}: use letters, digits and '-', "
            "starting with a letter or digit"
        )
    directory = selected.workflows_dir / workflow_id
    cls = (
        _import_workflow(directory)
        if (directory / "workflow.py").is_file()
        else PromptWorkflow
    )
    for name in [*handlers(cls), *resources(cls), *prompts(cls)]:
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
        for name, method, function in _marked(cls, _TOOL_MARKER, "tool")
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


def _marked(
    cls: type[Workflow], marker: str, label: str
) -> list[tuple[str, str, Callable[..., Any]]]:
    """Return ``(name, method, function)`` for methods carrying a marker."""
    markers = (_TOOL_MARKER, _RESOURCE_MARKER, _PROMPT_MARKER)
    result: list[tuple[str, str, Callable[..., Any]]] = []
    seen: dict[str, str] = {}
    for method in sorted(dir(cls)):
        value = inspect.getattr_static(cls, method, None)
        name = getattr(value, marker, None)
        if not isinstance(name, str):
            continue
        if hasattr(Workflow, method):
            raise WorkflowError(
                f"@{label} method {cls.__name__}.{method} shadows a Workflow "
                f'attribute; rename the method and keep the name with @{label}("'
                f'{name}")'
            )
        if sum(isinstance(getattr(value, item, None), str) for item in markers) > 1:
            raise WorkflowError(
                f"{cls.__name__}.{method} can be only one of a tool, resource, "
                "or MCP prompt"
            )
        if not _TOOL_NAME.fullmatch(name):
            raise WorkflowError(
                f"Invalid {label} name {name!r} on {cls.__name__}.{method}: use "
                "letters, digits, '-' and '_'"
            )
        if name in seen:
            raise WorkflowError(
                f"{label.capitalize()} name {name!r} is used by both "
                f"{cls.__name__}.{seen[name]} and {cls.__name__}.{method}"
            )
        if not inspect.isfunction(value):
            raise WorkflowError(f"@{label} {cls.__name__}.{method} must be a method")
        seen[name] = method
        result.append((name, method, value))
    return result


@cache
def resources(cls: type[Workflow]) -> dict[str, WorkflowResource]:
    """Collect the `resource` methods of a workflow class.

    Raises:
        hiraku.errors.WorkflowError: If a resource method takes parameters.
    """
    collected = {}
    for name, method, function in _marked(cls, _RESOURCE_MARKER, "resource"):
        if len(inspect.signature(function).parameters) != 1:
            raise WorkflowError(
                f"@resource {cls.__name__}.{method} takes no parameters besides self"
            )
        collected[name] = WorkflowResource(
            name=name,
            method=method,
            function=function,
            description=inspect.getdoc(function) or "",
            mime_type=getattr(function, _MIME_MARKER),
        )
    return collected


@cache
def prompts(cls: type[Workflow]) -> dict[str, WorkflowPrompt]:
    """Collect the `prompt` methods of a workflow class.

    Raises:
        hiraku.errors.WorkflowError: If a parameter is not a string.
    """
    collected = {}
    for name, method, function in _marked(cls, _PROMPT_MARKER, "prompt"):
        label = f"{cls.__name__}.{method}"
        hints = typing.get_type_hints(function)
        fields: dict[str, Any] = {}
        for parameter in list(inspect.signature(function).parameters.values())[1:]:
            if hints.get(parameter.name) not in (str, str | None):
                raise WorkflowError(
                    f"@prompt {label} parameter {parameter.name!r} must be a "
                    "str; MCP prompt arguments are strings"
                )
            default = (
                ...
                if parameter.default is inspect.Parameter.empty
                else parameter.default
            )
            fields[parameter.name] = (hints[parameter.name], default)
        collected[name] = WorkflowPrompt(
            name=name,
            method=method,
            function=function,
            description=inspect.getdoc(function) or "",
            arguments=create_model(
                f"{cls.__name__}_{method}_prompt",
                __config__=ConfigDict(extra="forbid"),
                **fields,
            ),
        )
    return collected


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
