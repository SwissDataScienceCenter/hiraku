"""Workflows: a ``workflow.md`` record whose ``start`` and events run on sessions."""

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
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeVar

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
    EventRejected,
    HirakuError,
    NotReady,
    ValidationError,
    WorkflowError,
)
from .markdown import placeholders, render
from .session import (
    HistoryEntry,
    Session,
    Timeout,
    locked,
    new_session_id,
    validate_session_id,
)

if TYPE_CHECKING:
    from .config import HirakuConfig

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])
_STATE_MARKER = "__hiraku_state__"
_EVENT_MARKER = "__hiraku_event__"
# No underscores, so the tool named after a workflow cannot collide with fixed
# tool names such as workflow_send_event.
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
# States are Mermaid state IDs in workflow graphs.
_STATE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def state(name: str, *, start: bool = False, final: bool = False) -> Callable[[_F], _F]:
    """Declare a state of a workflow; the method builds the state's instruction.

    The method returns the `Instruction` the agent receives in the state. It
    takes the workflow's data, annotated with the model ``start`` returns, or
    nothing if the workflow has no data. It reads the data and never changes
    it.

    Args:
        name: The state's name: letters, digits, and ``_``.
        start: Whether sessions start in this state; exactly one state does.
        final: Whether the state ends the session. A final state accepts no
            events, and its instruction is final: the agent carries it out,
            and its context is the session's result.

    Example:
        ```python
        @state("waiting")
        def ask(self, counter: Counter) -> Instruction:
            return Instruction(self.markdown.section("## Next"))
        ```
    """
    if not isinstance(name, str) or not _STATE_NAME.fullmatch(name):
        raise WorkflowError(
            f"State names use letters, digits, and '_', starting with a letter; "
            f"got {name!r}"
        )
    spec = StateSpec(name=name, start=bool(start), final=bool(final))

    def mark(method: _F) -> _F:
        setattr(method, _STATE_MARKER, spec)
        return method

    return mark


def event(
    name: str | None = None,
    /,
    *,
    source: str | Sequence[str],
    dest: str | Sequence[str],
    requires_approval: bool = False,
) -> Callable[[_F], _F]:
    """Declare an event: a transition from source states to destination states.

    The method continues a session: it takes the workflow's data, annotated
    with the model ``start`` returns, unless the workflow has none; its other
    parameters and its docstring define the event's arguments and
    description. It changes the data in place. The current state accepts the
    event if it is one of ``source``.

    With one destination, the method returns nothing and the session moves
    there. With several, it returns the one it chooses.

    Args:
        name: The event's name; the method's name by default. Several methods
            may share a name if their source states do not overlap: the
            event then runs the method of the current state, so each state
            can handle it, and describe it, in its own way.
        source: The states that accept the event.
        dest: The states the event may move to.
        requires_approval: Whether the user must approve the event before it
            runs. `Workflow.run_event` then answers ``approval_required``
            until it is called with ``approved=True``, which a transport does
            only after asking the user, e.g. through MCP elicitation.

    Example:
        ```python
        @event(source="running", dest=["waiting", "finished"])
        def ack(self, counter: Counter, token: str) -> str:
            return "finished" if counter.step >= counter.count else "waiting"

        @event("revise", source="draft", dest="draft")
        def revise_draft(self, experiments: Experiments, feedback: str) -> None:
            experiments.feedback = feedback
        ```
    """
    if name is not None and (
        not isinstance(name, str) or not _STATE_NAME.fullmatch(name)
    ):
        raise WorkflowError(
            f"Event names use letters, digits, and '_', starting with a letter; "
            f"got {name!r}"
        )
    declared = (
        name,
        _state_names(source, "source"),
        _state_names(dest, "dest"),
        bool(requires_approval),
    )

    def mark(method: _F) -> _F:
        setattr(method, _EVENT_MARKER, declared)
        return method

    return mark


def _state_names(value: str | Sequence[str], label: str) -> tuple[str, ...]:
    names = (value,) if isinstance(value, str) else tuple(value)
    if not names or not all(isinstance(name, str) for name in names):
        raise WorkflowError(f"@event {label} must name states, got {value!r}")
    return names


@dataclass(frozen=True)
class StateSpec:
    """What `state` declares: a state's name and whether it starts or ends."""

    name: str
    start: bool = False
    final: bool = False


@dataclass(frozen=True)
class State:
    """A `state` method of a workflow."""

    name: str
    function: Callable[..., Any]
    start: bool
    final: bool


@dataclass(frozen=True)
class Event:
    """An `event` method of a workflow and its derived argument model.

    Attributes:
        name: The event's name, which agents and users send.
        method: The method's name; it differs from ``name`` when several
            methods handle an event in different states.
        source: States that accept the event.
        dest: States the event may move to.
        requires_approval: Whether the user must approve the event first.
    """

    name: str
    method: str
    function: Callable[..., Any]
    arguments: type[BaseModel]
    description: str
    source: tuple[str, ...]
    dest: tuple[str, ...]
    requires_approval: bool = False


@dataclass(frozen=True)
class Instruction:
    """What the agent does in a state, returned by a `state` method.

    Attributes:
        text: Actionable guidance, usually a section of ``workflow.md``.
        context: Concise JSON-compatible data the text refers to. In a final
            state, it is the session's result.
        events: Narrows the events the state accepts, e.g.
            ``[self.finish]`` while a step is not available yet; by default,
            the instruction accepts every event of its state.
        requires_user_response: Whether the agent must obtain a user answer
            before sending an event. This is a conversational contract only.
    """

    text: str
    context: Any = None
    events: Sequence[Callable[..., Any]] = ()
    requires_user_response: bool = False


class Workflow(BaseRecord):
    """A workflow: its ``workflow.md`` record, ``start``, states, and events.

    Subclass it in ``hiraku/workflows/<workflow-id>/workflow.py`` and export the
    class as ``WORKFLOW``. The subclass defines:

    - ``start``, whose signature and docstring define how the workflow is
      started, and which returns the workflow's data: an instance of the
      Pydantic model named in its return annotation, or ``None`` with
      ``-> None`` for a workflow without data;
    - `state` methods, which declare the states and build each state's
      `Instruction`; sessions begin in the state marked ``start``;
    - `event` methods, which move the session from a state to another and
      change the data.

    The runtime owns the current state: only declared events change it. The
    states and events also draw the workflow as a graph, see `workflow_graph`.

    An instance is the loaded ``workflow.md``: the front matter holds the
    workflow ``id``, which must match the directory name, and any settings the
    subclass declares as annotated fields; the Markdown body holds the prompt
    sections, selected by heading with ``self.markdown.section("## Step")``.
    The base class alone loads any ``workflow.md`` without importing project
    code. The runtime loads each workflow once and gives every call a fresh
    copy bound to the project; changes to an instance are not persisted.

    Note:
        Class bodies follow Pydantic rules: annotated attributes are front-matter
        fields, constants need ``ClassVar``, and ``__init__`` is not overridden.
    """

    _config: HirakuConfig | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def check_headings(self) -> Self:
        # Records may repeat headings; a workflow selects its prompts by heading,
        # so a repeated one is an authoring error, reported when it loads.
        seen: set[str] = set()
        for heading in map(str, self.markdown.headings):
            if heading in seen:
                raise ValidationError(
                    f"Workflow {self.id!r} has several sections '{heading}'; rename one"
                )
            seen.add(heading)
        return self

    def bind(self, config: HirakuConfig) -> Self:
        """Return a copy bound to a project."""
        bound = self.model_copy(deep=True)
        bound._config = config
        return bound

    def start_arguments(self) -> type[BaseModel]:
        """Return the model of the arguments of ``start``.

        It is derived from the signature of ``start``; `PromptWorkflow`
        overrides it with the arguments declared in ``workflow.md``.
        """
        return start_model(type(self))

    def run_start(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Start a session: call ``start``, enter the start state, and store it.

        The workflow must be bound to a project with `bind`.

        Args:
            arguments: JSON-compatible arguments of ``start``.

        Returns:
            A JSON-compatible response whose ``status`` is ``accepted``, with
            the first instruction, or ``rejected``, ``not_ready``, or ``error``,
            in which case no session is stored.
        """
        validated, failure = _validate(self.start_arguments(), arguments)
        if failure is not None:
            return failure
        start = getattr(self, "start")  # noqa: B009 - defined by subclasses
        data, failure = _invoke(self, "start", lambda: start(**_keywords(validated)))
        if failure is not None:
            return failure
        step, failure = self._enter(start_state(type(self)), data)
        if failure is not None:
            return failure
        now = datetime.now(UTC)
        session = Session(id=new_session_id(self.id), workflow=self.id, created_at=now)
        call = HistoryEntry(
            instruction_id=0,
            event="start",
            arguments=validated.model_dump(mode="json"),
            at=now,
        )
        session.advance(call, *step)
        session.save(config=self.config)
        return {"status": "accepted", **session.response}

    def run_event(
        self,
        session_id: str,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Send an event to the current instruction of a stored session.

        The event changes the session's data and moves it to a destination
        state, whose instruction comes next. The session is locked while this
        runs, and saved only if all of it succeeds. The workflow must be bound
        to a project with `bind`.

        Args:
            session_id: Session to continue.
            instruction_id: The instruction the event answers.
            event: Name of an `event` method the instruction accepts.
            arguments: JSON-compatible arguments of the event.
            approved: Whether the user approved the event. Pass it only after
                asking the user, never on the agent's word.

        Returns:
            A JSON-compatible response whose ``status`` is ``accepted``,
            ``rejected``, ``not_ready``, ``stale``, ``error``, or
            ``approval_required``, with the session's current instruction.
            ``approval_required`` answers an event that `event` declares with
            ``requires_approval`` and that is not ``approved``; its
            ``approval`` block describes what the user approves. Nothing is
            saved, so the event can be sent again once approved.

        Raises:
            hiraku.errors.ValidationError: If the session or instruction ID is
                malformed.
            hiraku.errors.SessionNotFoundError: If the session does not exist.
        """
        validate_session_id(session_id)
        if isinstance(instruction_id, bool) or not isinstance(instruction_id, int):
            raise ValidationError("instruction_id must be an integer")
        try:
            with locked(session_id, config=self.config):
                session = Session.load(session_id, config=self.config)
                if session.workflow != self.id:
                    return session.outcome(
                        "rejected",
                        f"Session {session_id!r} belongs to workflow "
                        f"{session.workflow!r}, not {self.id!r}",
                    )
                candidates = [
                    item
                    for item in event_methods(type(self)).values()
                    if item.name == event
                ]
                if not candidates:
                    return session.outcome(
                        "rejected",
                        f"{event!r} is not an event of workflow {self.id!r}; the "
                        "current instruction accepts: "
                        f"{', '.join(session.events) or 'none'}",
                    )
                # The current state's method handles the event; a retry after the
                # state moved on is recognized by check() below.
                method = next(
                    (item for item in candidates if session.state in item.source),
                    candidates[0],
                )
                validated, failure = _validate(method.arguments, arguments)
                if failure is not None:
                    return {**failure, **session.response}
                values = validated.model_dump(mode="json")
                early = session.check(event, instruction_id, values)
                if early is not None:
                    return early
                if method.requires_approval and not approved:
                    return {
                        "status": "approval_required",
                        "message": f"The user must approve event {event!r} before "
                        "it runs; nothing was saved.",
                        "approval": {
                            "event": event,
                            "description": method.description,
                            "arguments": values,
                        },
                        **session.response,
                    }
                try:
                    data = _load_data(type(self), session.data)
                except PydanticValidationError as exc:
                    return session.outcome("rejected", _incompatible(exc))
                own = () if data is None else (data,)
                returned, failure = _invoke(
                    self,
                    event,
                    lambda: method.function(self, *own, **_keywords(validated)),
                )
                if failure is not None:
                    return {**failure, **session.response}
                dest, problem = _destination(self, method, returned)
                if problem is not None:
                    return session.outcome(
                        "error", f"{problem}. No session progress was saved."
                    )
                step, failure = self._enter(dest, data)
                if failure is not None:
                    return {**failure, **session.response}
                call = HistoryEntry(
                    instruction_id=instruction_id,
                    event=event,
                    arguments=values,
                    at=datetime.now(UTC),
                )
                session.advance(call, *step)
                session.save()
                return {"status": "accepted", **session.response}
        except Timeout:
            return {
                "status": "rejected",
                "message": f"Session {session_id!r} is busy with another call; retry "
                "after it finishes",
            }

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

    def _enter(
        self, name: str, data: Any
    ) -> tuple[
        tuple[str, dict[str, Any] | None, dict[str, Any], list[str]] | None, Any
    ]:
        """Enter a state: build its instruction for the data.

        Returns:
            The state, the data as JSON, the instruction as JSON, and the names
            of the events it accepts; or a failure status.
        """
        try:
            dumped = _dump_data(type(self), data)
        except WorkflowError as exc:
            return None, {"status": "error", "message": str(exc)}
        method = state_methods(type(self))[name]
        own = () if data is None else (data,)
        instruction, failure = _invoke(
            self, f"state {name!r}", lambda: method.function(self, *own)
        )
        if failure is not None:
            return None, failure
        try:
            if _dump_data(type(self), data) != dumped:
                raise WorkflowError(
                    f"State {name!r} of workflow {self.id!r} changed the data; "
                    "change it only in start and events"
                )
            rendered, names = self._render(instruction, method)
        except WorkflowError as exc:
            return None, {"status": "error", "message": str(exc)}
        return (name, dumped, rendered, names), None

    def _render(
        self, instruction: Any, method: State
    ) -> tuple[dict[str, Any], list[str]]:
        """Return a state's instruction as JSON, and the events it accepts."""
        if not isinstance(instruction, Instruction):
            raise WorkflowError(
                f"State {method.name!r} of workflow {self.id!r} returned "
                f"{type(instruction).__name__}; it must return an Instruction"
            )
        text = instruction.text
        if not isinstance(text, str) or not text.strip():
            raise WorkflowError("Instruction text must not be empty")
        listed = [self._listed_event(item) for item in instruction.events]
        if len(listed) != len(set(listed)):
            raise WorkflowError("An instruction lists the same event more than once")
        if method.final and (listed or instruction.requires_user_response):
            raise WorkflowError(
                f"The instruction of final state {method.name!r} accepts no events "
                "and requires no user response"
            )
        names = [] if method.final else _accepted(type(self), method.name, listed)
        rendered: dict[str, Any] = {
            "text": text.strip(),
            "context": _ensure_json(instruction.context, "Instruction context"),
        }
        if names:
            # How to answer; the session adds its session and instruction IDs.
            specs = _event_specs(self, method.name)
            rendered["events"] = {
                "requires_user_response": bool(instruction.requires_user_response),
                "accepted": [{"event": name, **specs[name]} for name in names],
            }
        return rendered, names

    def _listed_event(self, method: Callable[..., Any]) -> Event:
        """Return the event method an instruction lists."""
        function = getattr(method, "__func__", None)
        registered = event_methods(type(self)).get(getattr(function, "__name__", ""))
        if (
            getattr(method, "__self__", None) is not self
            or registered is None
            or registered.function is not function
        ):
            raise WorkflowError(
                f"Events must be @event methods of this workflow, e.g. "
                f"self.approve; got {method!r}"
            )
        return registered


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


class PromptArguments(BaseModel):
    """The data of a Markdown workflow: its start arguments."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class PromptWorkflow(Workflow):
    """Send the whole workflow.md as one final instruction.

    Used for workflow directories that contain only ``workflow.md``. Its front
    matter may declare start ``arguments``, which fill ``{{name}}``
    placeholders in the body. The workflow has a single state, ``done``, which
    is final: the session completes when it starts, the agent then follows the
    prompt, and Hiraku checks nothing about the work.

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

    def start(self, **arguments: Any) -> PromptArguments:
        """Start the workflow; its whole prompt arrives as one instruction."""
        return PromptArguments(arguments=arguments)

    @state("done", start=True, final=True)
    def prompt(self, data: PromptArguments) -> Instruction:
        return Instruction(
            render(self.text, data.arguments), context=data.arguments or None
        )

    def start_arguments(self) -> type[BaseModel]:
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
            imported, or ``start``, ``next``, an event, or the states are
            invalid.
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
    check_workflow(cls)
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
    workflow.start_arguments()
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


# Signatures and declarations -----------------------------------------------------


@cache
def start_model(cls: type[Workflow]) -> type[BaseModel]:
    """Return the model of the arguments of a workflow class's ``start``.

    Raises:
        hiraku.errors.WorkflowError: If ``start`` is missing or its signature
            is unsupported.
    """
    function = inspect.getattr_static(cls, "start", None)
    if not inspect.isfunction(function):
        raise WorkflowError(f"Workflow {cls.__name__} must define a start method")
    label = f"{cls.__name__}.start"
    return _arguments_model(
        label, _parameters(label, function), _hints(label, function)
    )


@cache
def data_model(cls: type[Workflow]) -> type[BaseModel] | None:
    """Return the data model of a workflow class: what ``start`` returns.

    Returns:
        The Pydantic model, or ``None`` for a workflow declared without data,
        ``def start(...) -> None``.

    Raises:
        hiraku.errors.WorkflowError: If ``start`` is missing or does not
            declare its return type as a Pydantic model or ``None``.
    """
    function = inspect.getattr_static(cls, "start", None)
    if not inspect.isfunction(function):
        raise WorkflowError(f"Workflow {cls.__name__} must define a start method")
    hints = _hints(f"{cls.__name__}.start", function)
    model = hints.get("return", inspect.Parameter.empty)
    if model is None or model is type(None):
        return None
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise WorkflowError(
            f"{cls.__name__}.start must declare the workflow's data as its return "
            "type, a Pydantic model, e.g. `def start(self, ...) -> Counter`, or "
            "`-> None` for a workflow without data"
        )
    return model


@cache
def state_methods(cls: type[Workflow]) -> dict[str, State]:
    """Collect and validate the `state` methods of a workflow class, by state.

    Raises:
        hiraku.errors.WorkflowError: If a state is declared twice or a method's
            signature does not match the workflow's data.
    """
    found: dict[str, State] = {}
    for name, function, spec in _marked(cls, _STATE_MARKER):
        label = f"{cls.__name__}.{name}"
        parameters = _data_parameter(cls, label, "@state", function)
        if parameters:
            raise WorkflowError(
                f"@state {label} must take only the workflow's data"
                if data_model(cls) is not None
                else f"@state {label} must take no parameters; the workflow has no data"
            )
        if spec.name in found:
            raise WorkflowError(
                f"State {spec.name!r} of {cls.__name__} is declared twice, by "
                f"{found[spec.name].function.__name__} and {name}"
            )
        found[spec.name] = State(
            name=spec.name, function=function, start=spec.start, final=spec.final
        )
    return found


@cache
def event_methods(cls: type[Workflow]) -> dict[str, Event]:
    """Collect and validate the `event` methods of a workflow class.

    Events are returned by method name, in the order the class defines them;
    `Event.name` is the name agents and users send.

    Raises:
        hiraku.errors.WorkflowError: If an event's signature is unsupported or
            does not match the workflow's data, or its name is taken.
    """
    found: dict[str, Event] = {}
    for name, function, declared in _marked(cls, _EVENT_MARKER):
        event_name, source, dest, requires_approval = declared
        label = f"{cls.__name__}.{name}"
        parameters = _data_parameter(cls, label, "@event", function)
        found[name] = Event(
            name=event_name or name,
            method=name,
            function=function,
            arguments=_arguments_model(label, parameters, _hints(label, function)),
            description=inspect.getdoc(function) or "",
            source=source,
            dest=dest,
            requires_approval=requires_approval,
        )
    return found


def _marked(cls: type[Workflow], marker: str) -> list[tuple[str, Any, Any]]:
    """Return the methods of a class that carry a marker, in definition order."""
    names: dict[str, None] = {}
    for klass in reversed(cls.__mro__):
        names.update(dict.fromkeys(vars(klass)))
    found = []
    for name in names:
        function = inspect.getattr_static(cls, name, None)
        value = getattr(function, marker, None)
        if value is None:
            continue
        kind = "@state" if marker == _STATE_MARKER else "@event"
        if name == "start" or hasattr(Workflow, name):
            raise WorkflowError(
                f"{kind} {cls.__name__}.{name} is named like a Workflow method; "
                "rename it"
            )
        if not inspect.isfunction(function):
            raise WorkflowError(f"{kind} {cls.__name__}.{name} must be a method")
        found.append((name, function, value))
    return found


def _data_parameter(
    cls: type[Workflow], label: str, kind: str, function: Callable[..., Any]
) -> list[inspect.Parameter]:
    """Check a method's data parameter; return the parameters after it."""
    parameters = _parameters(label, function)
    model = data_model(cls)
    if model is None:
        return parameters
    first = parameters[0] if parameters else None
    if (
        first is None
        or first.kind is inspect.Parameter.KEYWORD_ONLY
        or _hints(label, function).get(first.name) is not model
    ):
        raise WorkflowError(
            f"{kind} {label} must take the workflow's data as its first parameter, "
            f"annotated with {model.__name__}"
        )
    return parameters[1:]


@cache
def check_workflow(cls: type[Workflow]) -> None:
    """Check that a workflow class declares a consistent state machine.

    Raises:
        hiraku.errors.WorkflowError: If ``start``, a state, or an event is
            invalid; there is not exactly one start state or no final state;
            an event names an unknown state; a final state accepts events or
            another state accepts none; or a state cannot be reached from the
            start state.
    """
    # The start arguments are checked per instance by start_arguments(), which
    # PromptWorkflow takes from its front matter instead of the signature.
    data_model(cls)
    states = state_methods(cls)
    events = event_methods(cls)
    if not states:
        raise WorkflowError(
            f"Workflow {cls.__name__} declares no states; mark the methods that "
            'build instructions with @state("name")'
        )
    starts = [name for name, item in states.items() if item.start]
    if len(starts) != 1:
        raise WorkflowError(
            f"Workflow {cls.__name__} needs exactly one state with start=True; it "
            f"has {', '.join(starts) or 'none'}"
        )
    if not any(item.final for item in states.values()):
        raise WorkflowError(
            f"Workflow {cls.__name__} needs a state with final=True, where sessions end"
        )
    for item in events.values():
        unknown = [name for name in (*item.source, *item.dest) if name not in states]
        if unknown:
            raise WorkflowError(
                f"@event {cls.__name__}.{item.method} names unknown states "
                f"{', '.join(unknown)}; states are {', '.join(states)}"
            )
    handlers: dict[tuple[str, str], str] = {}
    for item in events.values():
        for source in item.source:
            other = handlers.setdefault((item.name, source), item.method)
            if other != item.method:
                raise WorkflowError(
                    f"Event {item.name!r} of {cls.__name__} is handled twice in "
                    f"state {source!r}, by {other} and {item.method}; give the "
                    "methods of an event different source states"
                )
    for name, item in states.items():
        leaving = list(
            dict.fromkeys(
                event.name for event in events.values() if name in event.source
            )
        )
        if item.final and leaving:
            raise WorkflowError(
                f"Final state {name!r} of {cls.__name__} accepts {', '.join(leaving)}; "
                "a final state accepts no events"
            )
        if not item.final and not leaving:
            raise WorkflowError(
                f"State {name!r} of {cls.__name__} accepts no events; add an event "
                "with it as source, or mark it final=True"
            )
    reached = {starts[0]}
    frontier = [starts[0]]
    while frontier:
        current = frontier.pop()
        for item in events.values():
            if current in item.source:
                for target in item.dest:
                    if target not in reached:
                        reached.add(target)
                        frontier.append(target)
    unreachable = [name for name in states if name not in reached]
    if unreachable:
        raise WorkflowError(
            f"States of {cls.__name__} cannot be reached from {starts[0]!r}: "
            f"{', '.join(unreachable)}"
        )


def start_state(cls: type[Workflow]) -> str:
    """Return the name of a workflow class's start state."""
    return next(name for name, item in state_methods(cls).items() if item.start)


def workflow_graph(
    workflow: Workflow | type[Workflow], *, current: str | None = None
) -> str:
    """Draw the states and events of a workflow as a Mermaid state diagram.

    Each event becomes an edge from each of its source states to each of its
    destination states, labelled with the event's name, and with
    ``(approval)`` if the user must approve it.

    Args:
        workflow: A workflow, or its class.
        current: A state to highlight, e.g. a session's current state.
    """
    cls = workflow if isinstance(workflow, type) else type(workflow)
    check_workflow(cls)
    lines = ["stateDiagram-v2", f"  [*] --> {start_state(cls)}"]
    for item in event_methods(cls).values():
        label = f"{item.name} (approval)" if item.requires_approval else item.name
        for source in item.source:
            for dest in item.dest:
                lines.append(f"  {source} --> {dest}: {label}")
    lines.extend(
        f"  {name} --> [*]" for name, item in state_methods(cls).items() if item.final
    )
    if current is not None:
        lines += ["  classDef current fill:#fdff32", f"  class {current} current"]
    return "\n".join(lines) + "\n"


def _parameters(label: str, function: Callable[..., Any]) -> list[inspect.Parameter]:
    """Return the parameters of a method after ``self``."""
    if inspect.iscoroutinefunction(function):
        raise WorkflowError(f"{label} must be synchronous")
    parameters = list(inspect.signature(function).parameters.values())
    if not parameters or parameters[0].name != "self":
        raise WorkflowError(f"{label} must be an instance method")
    return parameters[1:]


def _hints(label: str, function: Callable[..., Any]) -> dict[str, Any]:
    try:
        return typing.get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as exc:
        raise WorkflowError(f"Cannot resolve annotations of {label}: {exc}") from exc


def _arguments_model(
    label: str, parameters: list[inspect.Parameter], hints: Mapping[str, Any]
) -> type[BaseModel]:
    """Derive the argument model of ``start`` or an event from its parameters."""
    fields: dict[str, Any] = {}
    for parameter in parameters:
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise WorkflowError(
                f"{label} cannot use *args, **kwargs, or positional-only "
                f"parameter {parameter.name!r}"
            )
        if parameter.name not in hints:
            raise WorkflowError(
                f"{label} parameter {parameter.name!r} needs a type annotation"
            )
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (hints[parameter.name], default)
    try:
        model = create_model(
            f"{label.replace('.', '_')}_arguments",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )
        model.model_json_schema()
    except Exception as exc:
        raise WorkflowError(
            f"Cannot derive an argument schema for {label}: {exc}"
        ) from exc
    return model


# Running start, states, and events --------------------------------------------------


def _validate(
    model: type[BaseModel], arguments: Mapping[str, Any] | None
) -> tuple[Any, dict[str, Any] | None]:
    """Return the validated arguments, or a failure status."""
    try:
        return model.model_validate({} if arguments is None else arguments), None
    except PydanticValidationError as exc:
        return None, {"status": "rejected", "message": validation_message(exc)}


def _invoke(
    workflow: Workflow, name: str, call: Callable[[], Any]
) -> tuple[Any, dict[str, Any] | None]:
    """Call ``start``, ``next``, or an event; return its result, or a failure."""
    try:
        return call(), None
    except NotReady as exc:
        return None, {"status": "not_ready", "message": str(exc)}
    except (HirakuError, PydanticValidationError) as exc:
        return None, {"status": "rejected", "message": str(exc)}
    except Exception as exc:
        logger.exception("Workflow %s %s failed", workflow.id, name)
        return None, {
            "status": "error",
            "message": (
                f"{name} of workflow {workflow.id} failed unexpectedly with "
                f"{type(exc).__name__}: {exc}. No session progress was saved."
            ),
        }


def _accepted(cls: type[Workflow], current: str, listed: list[Event]) -> list[str]:
    """Return the events a state accepts, narrowed to those listed."""
    accepted = list(
        dict.fromkeys(
            item.name for item in event_methods(cls).values() if current in item.source
        )
    )
    extra = [item.method for item in listed if current not in item.source]
    if extra:
        raise WorkflowError(
            f"State {current!r} does not accept {', '.join(extra)}; it accepts "
            f"{', '.join(accepted)}"
        )
    return [item.name for item in listed] or accepted


def _load_data(cls: type[Workflow], raw: dict[str, Any] | None) -> Any:
    """Return a session's stored data as the workflow's data model."""
    model = data_model(cls)
    return None if model is None else model.model_validate(raw)


def _dump_data(cls: type[Workflow], data: Any) -> dict[str, Any] | None:
    """Return the workflow's data as JSON, checking its type and round trip."""
    model = data_model(cls)
    if model is None:
        if data is not None:
            raise WorkflowError(
                f"Workflow {cls.__name__} declares no data (start returns None) but "
                f"produced a {type(data).__name__}"
            )
        return None
    if type(data) is not model:
        raise WorkflowError(
            f"Workflow {cls.__name__} produced {type(data).__name__} data; start "
            f"must return a {model.__name__}"
        )
    try:
        dumped = data.model_dump(mode="json")
        model.model_validate(dumped)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(
            f"Data of workflow {cls.__name__} does not round-trip through JSON: {exc}"
        ) from exc
    return dumped


def _destination(
    workflow: Workflow, method: Event, returned: Any
) -> tuple[str, str | None]:
    """Return the state an event moves to, or a description of its mistake."""
    label = f"Event {method.name!r} of workflow {workflow.id!r}"
    if len(method.dest) == 1:
        if returned is None or returned == method.dest[0]:
            return method.dest[0], None
        return "", (
            f"{label} returned {returned!r}; it moves to {method.dest[0]!r} and "
            "returns nothing"
        )
    if isinstance(returned, str) and returned in method.dest:
        return returned, None
    return "", (
        f"{label} must return the state it moves to, one of "
        f"{', '.join(method.dest)}; got {returned!r}"
    )


def compatibility_problem(workflow: Workflow, session: Session) -> str | None:
    """Check a stored session against the loaded workflow code.

    Returns:
        A description of the incompatibility, or ``None`` if the session can
        continue. Sessions continue with the currently loaded code; authors are
        responsible for keeping it compatible with existing sessions.
    """
    if session.state not in state_methods(type(workflow)):
        return (
            f"The loaded workflow no longer declares the state {session.state!r}. "
            "Inspect the session and start a new one."
        )
    registered = {item.name for item in event_methods(type(workflow)).values()}
    missing = [name for name in session.events if name not in registered]
    if missing:
        return (
            "The loaded workflow no longer defines the pending events "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    try:
        _load_data(type(workflow), session.data)
    except PydanticValidationError as exc:
        return _incompatible(exc)
    return None


def _incompatible(exc: PydanticValidationError) -> str:
    return (
        "The stored data no longer matches the loaded workflow: "
        f"{validation_message(exc)}. Inspect the session and start a new one."
    )


def _event_specs(workflow: Workflow, current: str) -> dict[str, dict[str, Any]]:
    """Return the description and argument schema of each event of a state."""
    return {
        method.name: {
            "description": summary(method.description),
            "arguments": _compact_schema(method.arguments),
            **({"requires_approval": True} if method.requires_approval else {}),
        }
        for method in event_methods(type(workflow)).values()
        if current in method.source
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


def instruction_text(instruction: Mapping[str, Any]) -> str:
    """Render an instruction of a response as text for an agent.

    The text comes first, then the context and, unless the instruction is
    final, the block of events it accepts as JSON.
    """
    parts = [instruction["text"]]
    for label, key in (("Context", "context"), ("Events", "events")):
        if instruction.get(key) is not None:
            value = json.dumps(instruction[key], indent=1, ensure_ascii=False)
            parts.append(f"{label}:\n```json\n{value}\n```")
    return "\n\n".join(parts)


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
    "Event",
    "EventRejected",
    "Instruction",
    "NotReady",
    "PromptArguments",
    "PromptWorkflow",
    "Session",
    "State",
    "Workflow",
    "check_workflow",
    "data_model",
    "discover_workflows",
    "event",
    "event_methods",
    "list_workflows",
    "load_workflow",
    "load_workflows",
    "start_state",
    "state",
    "state_methods",
    "workflow_graph",
]
