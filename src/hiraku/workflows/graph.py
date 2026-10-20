"""Compile explicit workflow declarations and render their phase graph."""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import Field, create_model

from ..errors import WorkflowError

if TYPE_CHECKING:
    from .workflow import Arguments, Instruction, State

_F = TypeVar("_F", bound=Callable[..., Any])
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def phase(name: str, *, final: bool = False) -> Callable[[_F], _F]:
    """Register an instruction method for a named phase."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise WorkflowError(f"Invalid phase name: {name!r}")

    def mark(function: _F) -> _F:
        function.__hiraku_phase__ = name, final
        return function

    return mark


def event(
    *, source: str | Sequence[str] | None, dest: str | Sequence[str]
) -> Callable[[_F], _F]:
    """Register possible edges. Only start has source=None."""

    def names(value: str | Sequence[str]) -> tuple[str, ...]:
        result = (value,) if isinstance(value, str) else tuple(value)
        if not result or any(
            not isinstance(name, str) or not _NAME.fullmatch(name) for name in result
        ):
            raise WorkflowError(f"Invalid event phases: {value!r}")
        return tuple(dict.fromkeys(result))

    sources = None if source is None else names(source)
    destinations = names(dest)

    def mark(function: _F) -> _F:
        function.__hiraku_event__ = sources, destinations
        return function

    return mark


@dataclass(frozen=True)
class PhaseDefinition:
    """A registered phase and whether it ends the session."""

    name: str
    final: bool
    handler: Callable[..., Instruction] = field(repr=False)


@dataclass(frozen=True)
class EventDefinition:
    """A registered event, its typed inputs, and possible graph edges."""

    name: str
    source: tuple[str, ...] | None
    dest: tuple[str, ...]
    arguments: type[Arguments] | None
    description: str
    handler: Callable[..., State] = field(repr=False)

    def __post_init__(self) -> None:
        if self.source is not None:
            object.__setattr__(self, "source", tuple(self.source))
        object.__setattr__(self, "dest", tuple(self.dest))


@dataclass(frozen=True)
class WorkflowGraph:
    """Immutable registry used by execution, tools, and diagrams."""

    state_model: type[State]
    phases: Mapping[str, PhaseDefinition]
    events: Mapping[str, EventDefinition]

    def __post_init__(self) -> None:
        from .workflow import Arguments, State

        object.__setattr__(self, "phases", MappingProxyType(dict(self.phases)))
        object.__setattr__(self, "events", MappingProxyType(dict(self.events)))
        if not isinstance(self.state_model, type) or not issubclass(
            self.state_model, State
        ):
            raise WorkflowError("A graph needs a State model")
        for definitions in (self.phases, self.events):
            for name, item in definitions.items():
                if (
                    not isinstance(name, str)
                    or name != item.name
                    or not _NAME.fullmatch(name)
                ):
                    raise WorkflowError(f"Invalid graph definition name: {name!r}")
                if not callable(item.handler) or inspect.iscoroutinefunction(
                    item.handler
                ):
                    raise WorkflowError(f"{name} needs a synchronous handler")
        if "start" not in self.events or self.start.source is not None:
            raise WorkflowError("A graph needs a start event with source=None")
        for item in self.events.values():
            if not item.dest or (item.name != "start" and not item.source):
                raise WorkflowError(f"{item.name} needs sources and destinations")
            if item.arguments is not None:
                if not isinstance(item.arguments, type) or not issubclass(
                    item.arguments, Arguments
                ):
                    raise WorkflowError(f"{item.name} needs an Arguments model")
                item.arguments.model_json_schema()
        phases, events = self.phases, self.events
        if not any(item.final for item in phases.values()):
            raise WorkflowError("A workflow needs a final phase")
        for item in events.values():
            unknown = set((item.source or ()) + item.dest) - phases.keys()
            if unknown:
                raise WorkflowError(
                    f"{item.name} names unknown phases: {sorted(unknown)}"
                )
        for item in phases.values():
            if bool(self.events_from(item.name)) == item.final:
                raise WorkflowError(
                    f"Phase {item.name!r} must accept events exactly when nonfinal"
                )
        reached = set(self.start.dest)
        while True:
            expanded = reached | {
                dest
                for item in events.values()
                if item.source and reached.intersection(item.source)
                for dest in item.dest
            }
            if expanded == reached:
                break
            reached = expanded
        if reached != phases.keys():
            raise WorkflowError(
                f"Unreachable phases: {sorted(phases.keys() - reached)}"
            )

    @property
    def start(self) -> EventDefinition:
        return self.events["start"]

    def events_from(self, phase: str) -> tuple[EventDefinition, ...]:
        """Return accepted events in declaration order."""
        return tuple(
            item
            for item in self.events.values()
            if item.source is not None and phase in item.source
        )

    def to_mermaid(self, *, current: str | None = None) -> str:
        """Render declared edges, optionally highlighting a phase."""
        lines = ["stateDiagram-v2"]
        for item in self.events.values():
            for source in item.source or ("[*]",):
                for dest in item.dest:
                    label = "" if item.source is None else f": {item.name}"
                    lines.append(f"  {source} --> {dest}{label}")
        lines.extend(
            f"  {item.name} --> [*]" for item in self.phases.values() if item.final
        )
        if current is not None:
            if current not in self.phases:
                raise WorkflowError(f"Unknown phase: {current!r}")
            lines.extend(
                (
                    "  classDef current fill:#fdff32",
                    f"  class {current} current",
                )
            )
        return "\n".join(lines) + "\n"

    @classmethod
    def from_decorated_methods(cls, workflow: type) -> WorkflowGraph:
        """Build a graph from a workflow class's decorated instance methods."""
        from .workflow import Arguments, Instruction, State, Workflow

        reserved = set(dir(Workflow))

        methods = {}
        for base in reversed(workflow.__mro__):
            methods.update(vars(base))
        start = methods.get("start")
        if not inspect.isfunction(start):
            raise WorkflowError(f"{workflow.__name__} must define a start event")
        try:
            model = typing.get_type_hints(start).get("return")
            if not isinstance(model, type) or not issubclass(model, State):
                raise WorkflowError("start must return a concrete State model")
            phases = {}
            events = {}
            for name, function in methods.items():
                node = getattr(function, "__hiraku_phase__", None)
                edge = getattr(function, "__hiraku_event__", None)
                if node is None and edge is None:
                    continue
                if node is not None and edge is not None:
                    raise WorkflowError(f"{name} cannot be both a phase and event")
                if name in reserved or not inspect.isfunction(function):
                    raise WorkflowError(f"Invalid workflow handler: {name}")
                hints = typing.get_type_hints(function, include_extras=True)
                parameters = list(inspect.signature(function).parameters.values())
                if (
                    inspect.iscoroutinefunction(function)
                    or not parameters
                    or parameters[0].name != "self"
                    or parameters[0].kind is not parameters[0].POSITIONAL_OR_KEYWORD
                ):
                    raise WorkflowError(f"{name} must be a synchronous instance method")
                parameters = parameters[1:]
                expected = [] if name == "start" else [model]
                if node is not None:
                    if name == "start":
                        raise WorkflowError("start must be an event")
                    if node[0] in phases:
                        raise WorkflowError(f"Duplicate phase: {node[0]}")
                    phases[node[0]] = PhaseDefinition(*node, function)
                    result = Instruction
                else:
                    source, dest = edge
                    if (source is None) != (name == "start"):
                        raise WorkflowError("Only start must have source=None")
                    inputs = parameters[len(expected) :]
                    fields = {}
                    for parameter in inputs:
                        if (
                            parameter.kind is not parameter.KEYWORD_ONLY
                            or parameter.name not in hints
                            or parameter.name.startswith("_")
                            or hasattr(Arguments, parameter.name)
                        ):
                            raise WorkflowError(
                                f"{name}: inputs must be annotated keyword-only "
                                "parameters with valid argument names"
                            )
                        annotation = hints[parameter.name]
                        if typing.get_origin(annotation) is typing.Annotated:
                            base, *metadata = typing.get_args(annotation)
                            descriptions = [m for m in metadata if isinstance(m, str)]
                            if len(descriptions) > 1:
                                raise WorkflowError(
                                    f"{name}.{parameter.name} has multiple descriptions"
                                )
                            if descriptions:
                                annotation = typing.Annotated[
                                    base,
                                    *(m for m in metadata if not isinstance(m, str)),
                                    Field(description=descriptions[0]),
                                ]
                        fields[parameter.name] = (
                            annotation,
                            ...
                            if parameter.default is parameter.empty
                            else parameter.default,
                        )
                    arguments = create_model(
                        f"{name}Arguments", __base__=Arguments, **fields
                    )
                    parameters = parameters[: len(expected)]
                    events[name] = EventDefinition(
                        name,
                        source,
                        dest,
                        arguments,
                        inspect.getdoc(function) or "",
                        function,
                    )
                    result = model
                if (
                    [hints.get(p.name) for p in parameters] != expected
                    or any(p.kind is not p.POSITIONAL_OR_KEYWORD for p in parameters)
                    or hints.get("return") is not result
                    or any(p.default is not p.empty for p in parameters)
                ):
                    raise WorkflowError(f"Invalid signature for {name}")
        except (NameError, TypeError, ValueError) as exc:
            raise WorkflowError(f"Invalid workflow {workflow.__name__}: {exc}") from exc
        if "start" not in events:
            raise WorkflowError("start must be decorated with @event(source=None, ...)")
        return cls(model, phases, events)
