"""Compile explicit workflow declarations and render their phase graph."""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..errors import ValidationError, WorkflowError

_F = TypeVar("_F", bound=Callable[..., Any])
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EventArguments(BaseModel):
    """Typed event inputs. Unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_text(cls, values: Mapping[str, str]) -> Self:
        """Parse text-only inputs: strings stay literal; other fields require JSON.

        Missing fields use model defaults. Unknown fields and decoded values
        are validated by the argument model.
        """
        arguments = {}
        for name, value in values.items():
            field = cls.model_fields.get(name)
            if field is not None and not _is_text(field.annotation):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"{name}: expected JSON ({exc.msg})") from exc
            arguments[name] = value
        return cls.model_validate(arguments)


def _is_text(annotation: Any) -> bool:
    if annotation is str:
        return True
    origin = typing.get_origin(annotation)
    arguments = [item for item in typing.get_args(annotation) if item is not type(None)]
    if origin is Literal:
        return all(isinstance(item, str) for item in arguments)
    if origin in (typing.Union, types.UnionType):
        return any(_is_text(item) for item in arguments)
    return False


def phase(name: str, *, final: bool = False) -> Callable[[_F], _F]:
    """Register an instruction method for a named phase."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise WorkflowError(f"Invalid phase name: {name!r}")

    def mark(function: _F) -> _F:
        function.__hiraku_phase__ = name, final
        return function

    return mark


def _phase_names(value: str | Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, (str, Sequence)):
        raise WorkflowError(f"Invalid event phases: {value!r}")
    result = (value,) if isinstance(value, str) else tuple(value)
    if not result or any(
        not isinstance(name, str) or not _NAME.fullmatch(name) for name in result
    ):
        raise WorkflowError(f"Invalid event phases: {value!r}")
    return tuple(dict.fromkeys(result))


def start(*, dest: str | Sequence[str]) -> Callable[[_F], _F]:
    """Register the start method and its possible initial phases."""
    destinations = _phase_names(dest)

    def mark(function: _F) -> _F:
        function.__hiraku_event__ = None, destinations
        return function

    return mark


def event(
    *, source: str | Sequence[str], dest: str | Sequence[str]
) -> Callable[[_F], _F]:
    """Register an ordinary event's possible graph edges."""
    sources, destinations = _phase_names(source), _phase_names(dest)

    def mark(function: _F) -> _F:
        function.__hiraku_event__ = sources, destinations
        return function

    return mark


@dataclass(frozen=True)
class Phase:
    """A registered phase and whether it ends the session."""

    name: str
    final: bool
    handler: Callable[..., BaseModel] = field(repr=False)


@dataclass(frozen=True)
class Event:
    """A registered event, its typed inputs, and possible graph edges."""

    name: str
    source: tuple[str, ...] | None
    dest: tuple[str, ...]
    arguments: type[EventArguments]
    description: str
    handler: Callable[..., BaseModel] = field(repr=False)

    def __post_init__(self) -> None:
        if self.source is not None:
            object.__setattr__(self, "source", tuple(self.source))
        object.__setattr__(self, "dest", tuple(self.dest))


@dataclass(frozen=True)
class WorkflowGraph:
    """Immutable registry used by execution, tools, and diagrams."""

    state_model: type[BaseModel]
    phases: Mapping[str, Phase]
    events: Mapping[str, Event]

    def __post_init__(self) -> None:
        object.__setattr__(self, "phases", MappingProxyType(dict(self.phases)))
        object.__setattr__(self, "events", MappingProxyType(dict(self.events)))
        for definitions in (self.phases, self.events):
            for name, item in definitions.items():
                if (
                    not isinstance(name, str)
                    or name != item.name
                    or not _NAME.fullmatch(name)
                ):
                    raise WorkflowError(f"Invalid graph definition name: {name!r}")
        if "start" not in self.events or self.start.source is not None:
            raise WorkflowError("A graph needs a start event with source=None")
        for item in self.events.values():
            if not item.dest or (item.name != "start" and not item.source):
                raise WorkflowError(f"{item.name} needs sources and destinations")
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

    def check_reachability(self) -> None:
        """Raise WorkflowError if any declared phases cannot be reached from start."""
        reached = set(self.start.dest)
        while True:
            expanded = reached | {
                dest
                for item in self.events.values()
                if item.source and reached.intersection(item.source)
                for dest in item.dest
            }
            if expanded == reached:
                break
            reached = expanded
        if reached != self.phases.keys():
            raise WorkflowError(
                f"Unreachable phases: {sorted(self.phases.keys() - reached)}"
            )

    @property
    def start(self) -> Event:
        return self.events["start"]

    def events_from(self, phase: str) -> tuple[Event, ...]:
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
    def from_class(cls, definition: type) -> WorkflowGraph:
        """Compile decorated instance methods into a graph.

        The start return annotation defines the Pydantic state model. Events
        accept and return that model; phases accept it and return any Pydantic
        model. No Workflow inheritance or execution semantics are required.
        """
        methods = {}
        for base in reversed(definition.__mro__):
            methods.update(vars(base))
        entry = methods.get("start")
        declaration = getattr(entry, "__hiraku_event__", None)
        if (
            not inspect.isfunction(entry)
            or declaration is None
            or declaration[0] is not None
        ):
            raise WorkflowError("The start method must be decorated with @start")
        try:
            model = typing.get_type_hints(entry).get("return")
            if not isinstance(model, type) or not issubclass(model, BaseModel):
                raise WorkflowError("start must return a Pydantic model")
            phases = {}
            events = {}
            for name, function in methods.items():
                node = getattr(function, "__hiraku_phase__", None)
                edge = getattr(function, "__hiraku_event__", None)
                if node is None and edge is None:
                    continue
                if node is not None and edge is not None:
                    raise WorkflowError(f"{name} cannot be both a phase and event")
                if not inspect.isfunction(function):
                    raise WorkflowError(f"Invalid graph handler: {name}")
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
                    if node[0] in phases:
                        raise WorkflowError(f"Duplicate phase: {node[0]}")
                    phases[node[0]] = Phase(*node, function)
                    result = hints.get("return")
                    if not isinstance(result, type) or not issubclass(
                        result, BaseModel
                    ):
                        raise WorkflowError(
                            f"Phase {name} must return a Pydantic model"
                        )
                else:
                    source, dest = edge
                    if (source is None) != (name == "start"):
                        raise WorkflowError("Only the start method can use @start")
                    arguments = _event_arguments(
                        function, parameters[len(expected) :], hints
                    )
                    parameters = parameters[: len(expected)]
                    events[name] = Event(
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
            raise WorkflowError(f"Invalid graph {definition.__name__}: {exc}") from exc
        return cls(model, phases, events)


def _event_arguments(
    function: Callable[..., Any],
    parameters: Sequence[inspect.Parameter],
    hints: Mapping[str, Any],
) -> type[EventArguments]:
    """Compile annotated keyword inputs into a model for validation and schemas."""
    fields = {}
    for parameter in parameters:
        name = f"{function.__name__}.{parameter.name}"
        if (
            parameter.kind is not parameter.KEYWORD_ONLY
            or parameter.name not in hints
            or parameter.name.startswith("_")
            or hasattr(EventArguments, parameter.name)
        ):
            raise WorkflowError(f"{name} must be an annotated keyword-only input")
        annotation = hints[parameter.name]
        if typing.get_origin(annotation) is typing.Annotated:
            base, *metadata = typing.get_args(annotation)
            descriptions = [m for m in metadata if isinstance(m, str)]
            if len(descriptions) > 1:
                raise WorkflowError(f"{name} has multiple descriptions")
            if descriptions:
                annotation = typing.Annotated[
                    base,
                    *(m for m in metadata if not isinstance(m, str)),
                    Field(description=descriptions[0]),
                ]
        fields[parameter.name] = (
            annotation,
            ... if parameter.default is parameter.empty else parameter.default,
        )
    model = create_model(
        f"{function.__name__}EventArguments", __base__=EventArguments, **fields
    )
    model.model_json_schema()
    return model
