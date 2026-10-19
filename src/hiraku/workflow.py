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
_EVENT_MARKER = "__hiraku_event__"
# No underscores, so the tool named after a workflow cannot collide with fixed
# tool names such as workflow_send_event.
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
# States are Mermaid state IDs in workflow graphs.
_STATE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@overload
def event(function: _F, /) -> _F: ...


@overload
def event(
    *, source: str | Sequence[str], dest: str | Sequence[str]
) -> Callable[[_F], _F]: ...


def event(
    function: _F | None = None,
    /,
    *,
    source: str | Sequence[str] | None = None,
    dest: str | Sequence[str] | None = None,
) -> Any:
    """Mark a workflow method as an event that continues a session.

    The first parameter after ``self`` is the workflow's state, annotated with
    the model ``start`` returns. The other parameters and the docstring define
    the event's arguments and description. An event changes the state in place
    and returns nothing; ``next`` then builds the following instruction.

    Used bare, ``@event``, an event is accepted while the current instruction
    lists it, e.g. ``events=[self.approve]``. With ``source`` and ``dest``,
    e.g. ``@event(source="review", dest=["draft", "finished"])``, the
    workflow is a state machine: the ``state`` field of its state model names
    the current state, an event is accepted in its source states, and it must
    set ``state`` to one of its destination states. These states also draw the
    workflow as a graph.

    The decorated method stays directly callable from Python.
    """
    if function is not None:
        if source is not None or dest is not None:
            raise TypeError("Use @event or @event(source=..., dest=...)")
        setattr(function, _EVENT_MARKER, (None, None))
        return function
    if source is None or dest is None:
        raise WorkflowError("@event(...) needs both source and dest states")
    states = (_state_names(source), _state_names(dest))

    def mark(method: _F) -> _F:
        setattr(method, _EVENT_MARKER, states)
        return method

    return mark


def _state_names(value: str | Sequence[str]) -> tuple[str, ...]:
    names = (value,) if isinstance(value, str) else tuple(value)
    if not names or not all(isinstance(name, str) for name in names):
        raise WorkflowError(f"States must be names, got {value!r}")
    return names


@dataclass(frozen=True)
class Event:
    """An `event` method of a workflow and its derived argument model.

    Attributes:
        source: States that accept the event; ``None`` without states.
        dest: States the event may move to; ``None`` without states.
    """

    name: str
    function: Callable[..., Any]
    arguments: type[BaseModel]
    description: str
    source: tuple[str, ...] | None = None
    dest: tuple[str, ...] | None = None


@dataclass(frozen=True)
class States:
    """The states of a workflow whose events declare source and destination.

    Attributes:
        names: Every state, from the ``Literal`` type of the state model's
            ``state`` field.
        start: The default of that field, where every session starts.
        final: States that accept no event; their instruction is final.
    """

    names: tuple[str, ...]
    start: str
    final: tuple[str, ...]


@dataclass(frozen=True)
class Instruction:
    """What the agent does next, returned by a workflow's ``next``.

    Attributes:
        text: Actionable guidance, usually a section of ``workflow.md``.
        context: Concise JSON-compatible data the text refers to.
        events: `event` methods of the workflow the instruction accepts, e.g.
            ``[self.approve, self.stop]``. With states, it defaults to every
            event of the current state, and may narrow them.
        requires_user_response: Whether the agent must obtain a user answer
            before sending an event. This is a conversational contract only.
        final: Whether this is the last instruction. A final instruction
            accepts no events; returning it completes the session, and its
            context is the session's result. The agent still carries out its
            text.
    """

    text: str
    context: Any = None
    events: Sequence[Callable[..., Any]] = ()
    requires_user_response: bool = False
    final: bool = False


class Workflow(BaseRecord):
    """A workflow: its ``workflow.md`` record, ``start``, ``next``, and events.

    Subclass it in ``hiraku/workflows/<workflow-id>/workflow.py`` and export the
    class as ``WORKFLOW``. The subclass defines:

    - ``start``, whose signature and docstring define how the workflow is
      started, and which returns the first state of a new session: an instance
      of a Pydantic model, named in the return annotation of ``start``;
    - ``next(state)``, which returns the `Instruction` for a state; it reads
      the state and never changes it;
    - `event` methods, which change the state and return nothing. Declaring
      their source and destination states makes the workflow a state machine
      that `graph` draws.

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
        """Start a session: call ``start``, then ``next``, and store the session.

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
        state, failure = _invoke(self, "start", lambda: start(**_keywords(validated)))
        if failure is not None:
            return failure
        states = workflow_states(type(self))
        if states is not None and getattr(state, "state", None) != states.start:
            return {
                "status": "error",
                "message": f"start of workflow {self.id!r} must return a state in "
                f"its start state {states.start!r}",
            }
        step, failure = self._next(state)
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
    ) -> dict[str, Any]:
        """Send an event to the current instruction of a stored session.

        The event changes the session's state, and ``next`` builds the
        following instruction. The session is locked while this runs, and saved
        only if both succeed. The workflow must be bound to a project with
        `bind`.

        Args:
            session_id: Session to continue.
            instruction_id: The instruction the event answers.
            event: Name of an `event` method the instruction accepts.
            arguments: JSON-compatible arguments of the event.

        Returns:
            A JSON-compatible response whose ``status`` is ``accepted``,
            ``rejected``, ``not_ready``, ``stale``, or ``error``, with the
            session's current instruction.

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
                method = event_methods(type(self)).get(event)
                if method is None:
                    return session.outcome(
                        "rejected",
                        f"{event!r} is not an event of workflow {self.id!r}; the "
                        "current instruction accepts: "
                        f"{', '.join(session.events) or 'none'}",
                    )
                validated, failure = _validate(method.arguments, arguments)
                if failure is not None:
                    return {**failure, **session.response}
                values = validated.model_dump(mode="json")
                early = session.check(event, instruction_id, values)
                if early is not None:
                    return early
                try:
                    state = state_model(type(self)).model_validate(session.data)
                except PydanticValidationError as exc:
                    return session.outcome("rejected", _incompatible(exc))
                returned, failure = _invoke(
                    self,
                    event,
                    lambda: method.function(self, state, **_keywords(validated)),
                )
                if failure is not None:
                    return {**failure, **session.response}
                problem = _event_result(self, method, returned, state)
                if problem is not None:
                    return session.outcome(
                        "error", f"{problem}. No session progress was saved."
                    )
                step, failure = self._next(state)
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

    def _next(
        self, state: Any
    ) -> tuple[tuple[dict[str, Any], dict[str, Any], list[str]] | None, Any]:
        """Call ``next`` on a new state; return the session's next data.

        Returns:
            The state as JSON, the instruction as JSON, and the names of the
            events it accepts; or a failure status.
        """
        try:
            data = self._dump_state(state)
        except WorkflowError as exc:
            return None, {"status": "error", "message": str(exc)}
        next_ = getattr(self, "next")  # noqa: B009 - defined by subclasses
        instruction, failure = _invoke(self, "next", lambda: next_(state))
        if failure is not None:
            return None, failure
        try:
            if self._dump_state(state) != data:
                raise WorkflowError(
                    f"next of workflow {self.id!r} changed the state; change it "
                    "only in start and events"
                )
            rendered, names = self._render(instruction, state)
        except WorkflowError as exc:
            return None, {"status": "error", "message": str(exc)}
        return (data, rendered, names), None

    def _dump_state(self, state: Any) -> dict[str, Any]:
        model = state_model(type(self))
        if type(state) is not model:
            raise WorkflowError(
                f"Workflow {self.id!r} produced a {type(state).__name__} state; "
                f"start must return a {model.__name__}"
            )
        try:
            data = state.model_dump(mode="json")
            model.model_validate(data)
        except (TypeError, ValueError) as exc:
            raise WorkflowError(
                f"State of workflow {self.id!r} does not round-trip through JSON: {exc}"
            ) from exc
        return data

    def _render(self, instruction: Any, state: Any) -> tuple[dict[str, Any], list[str]]:
        """Return an instruction as JSON, and the events it accepts."""
        if not isinstance(instruction, Instruction):
            raise WorkflowError(
                f"next of workflow {self.id!r} returned "
                f"{type(instruction).__name__}; it must return an Instruction"
            )
        text = instruction.text
        if not isinstance(text, str) or not text.strip():
            raise WorkflowError("Instruction text must not be empty")
        names = [self._event_name(method) for method in instruction.events]
        if len(names) != len(set(names)):
            raise WorkflowError("An instruction lists the same event more than once")
        if instruction.final and (names or instruction.requires_user_response):
            raise WorkflowError(
                "A final instruction accepts no events and requires no user response"
            )
        states = workflow_states(type(self))
        if states is not None:
            names = self._state_events(state.state, names, instruction.final)
        if not instruction.final and not names:
            raise WorkflowError(
                "An instruction must accept at least one event, or be final"
            )
        rendered: dict[str, Any] = {
            "text": text.strip(),
            "context": _ensure_json(instruction.context, "Instruction context"),
        }
        if names:
            # How to answer; the session adds its session and instruction IDs.
            specs = _event_specs(self)
            rendered["events"] = {
                "requires_user_response": bool(instruction.requires_user_response),
                "accepted": [{"event": name, **specs[name]} for name in names],
            }
        return rendered, names

    def _state_events(self, current: str, listed: list[str], final: bool) -> list[str]:
        """Return the events a state accepts, narrowed to those listed."""
        accepted = [
            name
            for name, method in event_methods(type(self)).items()
            if method.source is not None and current in method.source
        ]
        if final:
            if accepted:
                raise WorkflowError(
                    f"State {current!r} accepts {', '.join(accepted)}; only a state "
                    "without events has a final instruction"
                )
            return []
        if not listed:
            return accepted
        extra = [name for name in listed if name not in accepted]
        if extra:
            raise WorkflowError(
                f"State {current!r} does not accept {', '.join(extra)}; it accepts "
                f"{', '.join(accepted) or 'no events'}"
            )
        return listed

    def _event_name(self, method: Callable[..., Any]) -> str:
        function = getattr(method, "__func__", None)
        name = getattr(function, "__name__", "")
        registered = event_methods(type(self)).get(name)
        if (
            getattr(method, "__self__", None) is not self
            or registered is None
            or registered.function is not function
        ):
            raise WorkflowError(
                f"Events must be @event methods of this workflow, e.g. "
                f"self.approve; got {method!r}"
            )
        return name


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


class PromptState(BaseModel):
    """The state of a Markdown workflow: its start arguments."""

    arguments: dict[str, Any] = Field(default_factory=dict)


class PromptWorkflow(Workflow):
    """Send the whole workflow.md as one final instruction.

    Used for workflow directories that contain only ``workflow.md``. Its front
    matter may declare start ``arguments``, which fill ``{{name}}``
    placeholders in the body. The session completes when it starts; the agent
    then follows the prompt, and Hiraku checks nothing about the work.

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

    def start(self, **arguments: Any) -> PromptState:
        """Start the workflow; its whole prompt arrives as one instruction."""
        return PromptState(arguments=arguments)

    def next(self, state: PromptState) -> Instruction:
        return Instruction(
            render(self.text, state.arguments),
            context=state.arguments or None,
            final=True,
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
    state_model(cls)
    workflow_states(cls)
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


# Signatures ----------------------------------------------------------------------


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
def state_model(cls: type[Workflow]) -> type[BaseModel]:
    """Return the state model of a workflow class: what ``start`` returns.

    Raises:
        hiraku.errors.WorkflowError: If ``start`` does not name a Pydantic model
            as its return type, or ``next`` is missing or does not take only
            the state.
    """
    start = inspect.getattr_static(cls, "start", None)
    if not inspect.isfunction(start):
        raise WorkflowError(f"Workflow {cls.__name__} must define a start method")
    model = _hints(f"{cls.__name__}.start", start).get("return")
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise WorkflowError(
            f"{cls.__name__}.start must declare its state as the return type, a "
            "Pydantic model, e.g. `def start(self, ...) -> State`"
        )
    function = inspect.getattr_static(cls, "next", None)
    label = f"{cls.__name__}.next"
    if not inspect.isfunction(function):
        raise WorkflowError(
            f"Workflow {cls.__name__} must define next(self, state), which returns "
            "the instruction for a state"
        )
    if len(_parameters(label, function)) != 1:
        raise WorkflowError(f"{label} must take only the state")
    return model


@cache
def event_methods(cls: type[Workflow]) -> dict[str, Event]:
    """Collect and validate the `event` methods of a workflow class.

    Events are returned in the order the class defines them.

    Raises:
        hiraku.errors.WorkflowError: If an event's signature is unsupported, its
            name is taken by `Workflow`, ``start``, or ``next``, or only some
            events declare states.
    """
    names: dict[str, None] = {}
    for klass in reversed(cls.__mro__):
        names.update(dict.fromkeys(vars(klass)))
    found: dict[str, Event] = {}
    for name in names:
        function = inspect.getattr_static(cls, name, None)
        states = getattr(function, _EVENT_MARKER, None)
        if states is None:
            continue
        label = f"{cls.__name__}.{name}"
        if name in ("start", "next") or hasattr(Workflow, name):
            raise WorkflowError(
                f"@event {label} is named like a Workflow method; rename it"
            )
        if not inspect.isfunction(function):
            raise WorkflowError(f"@event {label} must be a method")
        parameters = _parameters(label, function)
        hints = _hints(label, function)
        first = parameters[0] if parameters else None
        model = state_model(cls)
        if (
            first is None
            or first.kind is inspect.Parameter.KEYWORD_ONLY
            or hints.get(first.name) is not model
        ):
            raise WorkflowError(
                f"@event {label} must take the state as its first parameter, "
                f"annotated with {model.__name__}"
            )
        found[name] = Event(
            name=name,
            function=function,
            arguments=_arguments_model(label, parameters[1:], hints),
            description=inspect.getdoc(function) or "",
            source=states[0],
            dest=states[1],
        )
    without = [name for name, item in found.items() if item.source is None]
    if without and len(without) != len(found):
        raise WorkflowError(
            f"Declare source and dest for every event of {cls.__name__} or for "
            f"none; missing for {', '.join(without)}"
        )
    return found


@cache
def workflow_states(cls: type[Workflow]) -> States | None:
    """Return the states of a workflow class, or ``None`` if it has none.

    A workflow has states when its events declare ``source`` and ``dest``.
    The ``state`` field of its state model lists them as a ``Literal`` and
    defaults to the start state.

    Raises:
        hiraku.errors.WorkflowError: If the states are inconsistent: no typed
            ``state`` field, an unknown or malformed state, or a state that
            cannot be reached from the start state.
    """
    events = event_methods(cls)
    if not events or any(item.source is None for item in events.values()):
        return None
    model = state_model(cls)
    field = model.model_fields.get("state")
    names = (
        typing.get_args(field.annotation)
        if field is not None and typing.get_origin(field.annotation) is Literal
        else ()
    )
    if not names or not all(isinstance(name, str) for name in names):
        raise WorkflowError(
            f"The events of {cls.__name__} declare states, so {model.__name__} "
            "needs a state field listing them, e.g. "
            'state: Literal["running", "finished"] = "running"'
        )
    malformed = [name for name in names if not _STATE.fullmatch(name)]
    if malformed:
        raise WorkflowError(
            f"State names use letters, digits, and '_': {', '.join(malformed)}"
        )
    assert field is not None
    start = field.default
    if start not in names:
        raise WorkflowError(
            f"{model.__name__}.state needs a default, the start state; one of "
            f"{', '.join(names)}"
        )
    for item in events.values():
        unknown = [
            name
            for name in (*(item.source or ()), *(item.dest or ()))
            if name not in names
        ]
        if unknown:
            raise WorkflowError(
                f"@event {cls.__name__}.{item.name} names unknown states "
                f"{', '.join(unknown)}; states are {', '.join(names)}"
            )
    reached = {start}
    frontier = [start]
    while frontier:
        state = frontier.pop()
        for item in events.values():
            if state in (item.source or ()):
                for target in item.dest or ():
                    if target not in reached:
                        reached.add(target)
                        frontier.append(target)
    unreachable = [name for name in names if name not in reached]
    if unreachable:
        raise WorkflowError(
            f"States of {cls.__name__} cannot be reached from {start!r}: "
            f"{', '.join(unreachable)}"
        )
    final = tuple(
        name
        for name in names
        if not any(name in (item.source or ()) for item in events.values())
    )
    return States(names=names, start=start, final=final)


def workflow_graph(
    workflow: Workflow | type[Workflow], *, current: str | None = None
) -> str:
    """Draw the states of a workflow as a Mermaid state diagram.

    Each event becomes an edge from each of its source states to each of its
    destination states, labelled with the event's name.

    Args:
        workflow: A workflow, or its class.
        current: A state to highlight, e.g. a session's current state.

    Raises:
        hiraku.errors.WorkflowError: If the workflow has no states.
    """
    cls = workflow if isinstance(workflow, type) else type(workflow)
    states = workflow_states(cls)
    if states is None:
        raise WorkflowError(
            f"Workflow {cls.__name__} has no states to draw; declare source and "
            "dest on its events, e.g. @event(source=..., dest=...)"
        )
    lines = ["stateDiagram-v2", f"  [*] --> {states.start}"]
    for name, item in event_methods(cls).items():
        for source in item.source or ():
            for dest in item.dest or ():
                lines.append(f"  {source} --> {dest}: {name}")
    lines.extend(f"  {name} --> [*]" for name in states.final)
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


# Running start, next, and events --------------------------------------------------


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


def _event_result(
    workflow: Workflow, method: Event, returned: Any, state: Any
) -> str | None:
    """Describe what an event did wrong, or return ``None``."""
    if returned is not None:
        return (
            f"Event {method.name!r} of workflow {workflow.id!r} returned "
            f"{type(returned).__name__}; events change the state and return nothing"
        )
    current = getattr(state, "state", None)
    if method.dest is not None and current not in method.dest:
        return (
            f"Event {method.name!r} of workflow {workflow.id!r} moved to state "
            f"{current!r}; it declares dest {', '.join(method.dest)}"
        )
    return None


def compatibility_problem(workflow: Workflow, session: Session) -> str | None:
    """Check a stored session against the loaded workflow code.

    Returns:
        A description of the incompatibility, or ``None`` if the session can
        continue. Sessions continue with the currently loaded code; authors are
        responsible for keeping it compatible with existing sessions.
    """
    registered = event_methods(type(workflow))
    missing = [name for name in session.events if name not in registered]
    if missing:
        return (
            "The loaded workflow no longer defines the pending events "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    try:
        state_model(type(workflow)).model_validate(session.data)
    except PydanticValidationError as exc:
        return _incompatible(exc)
    return None


def _incompatible(exc: PydanticValidationError) -> str:
    return (
        "The stored state no longer matches the loaded workflow: "
        f"{validation_message(exc)}. Inspect the session and start a new one."
    )


def _event_specs(workflow: Workflow) -> dict[str, dict[str, Any]]:
    """Return the description and argument schema of each event, by name."""
    return {
        name: {
            "description": summary(method.description),
            "arguments": _compact_schema(method.arguments),
        }
        for name, method in event_methods(type(workflow)).items()
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
    "States",
    "PromptState",
    "PromptWorkflow",
    "Session",
    "Workflow",
    "discover_workflows",
    "event",
    "event_methods",
    "list_workflows",
    "load_workflow",
    "load_workflows",
    "workflow_graph",
    "workflow_states",
]
