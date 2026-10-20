"""Workflow definitions, loading, and transport-independent execution."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import logging
import re
import secrets
import sys
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from filelock import Timeout
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from ..base import BaseRecord
from ..config import HirakuConfig
from ..errors import EventRejected, NotReady, ValidationError, WorkflowError
from .graph import EventDefinition, WorkflowGraph

if TYPE_CHECKING:
    from .session import Session

logger = logging.getLogger(__name__)


class Arguments(BaseModel):
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


class State(BaseModel):
    """Complete persisted workflow state, including its graph phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    phase: str


class Instruction(BaseModel):
    """An assignment, its context, and the events that can answer it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1)
    context: Any = None
    events: dict[str, Any] | None = None

    def __init__(self, text: str, context: Any = None, **values: Any) -> None:
        super().__init__(
            text=text.strip() if isinstance(text, str) else text,
            context=context,
            **values,
        )

    def to_text(self) -> str:
        """Render this instruction, context, and accepted events for an agent."""
        parts = [self.text]
        fence = chr(96) * 3
        for label, value in (("Context", self.context), ("Events", self.events)):
            if value is not None:
                rendered = json.dumps(to_jsonable_python(value), indent=1)
                parts.append(f"{label}:\n{fence}json\n{rendered}\n{fence}")
        return "\n\n".join(parts)


class EventResult(BaseModel):
    """Submission outcome without an assignment; IDs name the current revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["accepted", "rejected", "not_ready", "stale", "error"]
    session_id: str | None = None
    instruction_id: int | None = None
    message: str | None = None


class Workflow(BaseRecord):
    """A Markdown definition bound to a project's configuration file.

    Construction builds its immutable graph from decorated methods.
    Configuration is reloaded on access; evolving data belongs in State.
    """

    description: str | None = None
    _graph: WorkflowGraph = PrivateAttr()
    _config_path: Path = PrivateAttr()

    def __init__(self, *, config: HirakuConfig | None = None, **values: Any) -> None:
        selected = config if config is not None else HirakuConfig.discover()
        super().__init__(**values)
        self._config_path = selected.path.absolute()
        self._graph = self.build_graph()

    @property
    def graph(self) -> WorkflowGraph:
        """Return this loaded definition's immutable execution graph."""
        return self._graph

    def build_graph(self) -> WorkflowGraph:
        """Build from decorators; subclasses may construct a graph explicitly."""
        return WorkflowGraph.from_decorated_methods(type(self))

    @model_validator(mode="after")
    def check_headings(self) -> Self:
        headings = list(map(str, self.markdown.headings))
        if len(headings) != len(set(headings)):
            raise ValidationError("Workflow Markdown must have unique headings")
        return self

    @property
    def config(self) -> HirakuConfig:
        """Reload the bound project's saved configuration."""
        return HirakuConfig.load(self._config_path)

    def run_start(self, arguments: Mapping[str, Any] | None = None) -> EventResult:
        """Run the entry event and store its instruction; return an outcome."""
        try:
            validated = (self.graph.start.arguments or Arguments).model_validate(
                {} if arguments is None else arguments
            )
        except PydanticValidationError as exc:
            return EventResult(status="rejected", message=validation_message(exc))
        return self._run(self.graph.start, validated)

    def run_event(
        self,
        session_id: str,
        instruction_id: int,
        event: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> EventResult:
        """Submit an event to a saved instruction, with correlation and retry checks."""
        from .session import _lock, _session_path, load_session

        config = self.config
        path = _session_path(session_id, config)
        if type(instruction_id) is not int or instruction_id < 1:
            raise ValidationError("instruction_id must be a positive integer")
        session = None
        try:
            with _lock(path):
                session = load_session(session_id, config=config)
                if session.workflow != self.id:
                    return session._result(
                        "rejected", "Session belongs to another workflow"
                    )
                if session.status == "cancelled":
                    return session._result("rejected", "Session is cancelled")
                method = self.graph.events.get(event)
                if method is None or method.source is None:
                    return session._result("rejected", f"Unknown event {event!r}")
                try:
                    validated = (method.arguments or Arguments).model_validate(
                        {} if arguments is None else arguments
                    )
                    values = validated.model_dump(mode="json")
                except PydanticValidationError as exc:
                    return session._result("rejected", validation_message(exc))
                last = session.history[-1]
                if last.instruction_id == instruction_id:
                    if last.event == event and last.arguments == values:
                        return session._result("accepted")
                    return session._result("stale", "Instruction was already answered")
                if session.status != "active":
                    return session._result("rejected", f"Session is {session.status}")
                if instruction_id != session.instruction_id:
                    return session._result("stale", "Instruction is no longer pending")
                if event not in session.events or session.phase not in method.source:
                    return session._result(
                        "rejected",
                        f"{event!r} is not an event the current instruction accepts",
                    )
                try:
                    self._check_session(session)
                except (ValueError, WorkflowError) as exc:
                    return session._result(
                        "rejected", f"Incompatible stored state: {exc}"
                    )
                return self._run(method, validated, session)
        except Timeout:
            return EventResult(
                status="not_ready",
                session_id=session_id,
                message="Session is busy; retry after the other call finishes",
            )

    def _run(
        self,
        method: EventDefinition,
        arguments: Arguments,
        session: Session | None = None,
    ) -> EventResult:
        from .session import HistoryEntry, Session, _session_path

        previous = session
        if session is not None:
            session = session.model_copy(deep=True)
        try:
            values = arguments.model_dump(mode="json")
            parameters = []
            if session is not None:
                parameters.append(self.graph.state_model.model_validate(session.state))
            inputs = {
                name: getattr(arguments, name) for name in type(arguments).model_fields
            }
            returned = method.handler(self, *parameters, **inputs)
            if type(returned) is not self.graph.state_model:
                raise WorkflowError(
                    f"{method.name} must return {self.graph.state_model.__name__}"
                )
            # Validate JSON, including updates made through model_copy(update=...).
            raw = returned.model_dump(mode="json", warnings=False)
            json.dumps(raw, allow_nan=False)
            state = self.graph.state_model.model_validate(raw)
            raw = state.model_dump(mode="json")
            if state.phase not in method.dest:
                raise WorkflowError(
                    f"{method.name} returned phase {state.phase!r}; "
                    f"expected {method.dest}"
                )
            node = self.graph.phases[state.phase]
            instruction = node.handler(self, state.model_copy(deep=True))
            if not isinstance(instruction, Instruction):
                raise WorkflowError(f"Phase {state.phase!r} must return Instruction")
            if instruction.events is not None:
                raise WorkflowError("Phase instructions cannot override graph events")
            events = self.graph.events_from(state.phase)
            instruction = instruction.model_copy(
                update={
                    "context": to_jsonable_python(instruction.context),
                    "events": {
                        "accepted": [
                            {
                                "event": item.name,
                                "description": summary(item.description),
                                "arguments": (
                                    item.arguments or Arguments
                                ).model_json_schema(),
                            }
                            for item in events
                        ]
                    }
                    if events
                    else None,
                }
            )
            json.dumps(instruction.model_dump(mode="json"), allow_nan=False)
            if session is None:
                session = Session(
                    id=f"{self.id}-{secrets.token_hex(6)}",
                    workflow=self.id,
                    state=raw,
                    instruction=instruction,
                )
                session._path = _session_path(session.id, self.config)
            session.history.append(
                HistoryEntry(
                    instruction_id=session.instruction_id,
                    event=method.name,
                    arguments=values,
                )
            )
            session.instruction_id += 1
            session.state = raw
            session.instruction = instruction
            session.status = "completed" if node.final else "active"
            session._save()
            return session._result("accepted")
        except (EventRejected, NotReady) as exc:
            status = "not_ready" if isinstance(exc, NotReady) else "rejected"
            if session is not None:
                return session._result(status, str(exc))
            return EventResult(status=status, message=str(exc))
        except Exception as exc:
            logger.exception("Workflow %s event %s failed", self.id, method.name)
            message = f"{type(exc).__name__}: {exc}. No session progress was saved."
            if previous is not None:
                return previous._result("error", message)
            return EventResult(status="error", message=message)

    def _check_session(self, session: Session) -> None:
        if session.workflow != self.id:
            raise WorkflowError("Session belongs to another workflow")
        state = self.graph.state_model.model_validate(session.state)
        if state.phase not in self.graph.phases:
            raise WorkflowError(f"Unknown phase {state.phase!r}")
        if not set(session.events).issubset(
            item.name for item in self.graph.events_from(state.phase)
        ):
            raise WorkflowError("Pending events no longer match the graph")

    def get_instruction(self, session_id: str) -> dict[str, Any]:
        """Read the saved assignment without executing any workflow handlers."""
        from .session import load_session

        session = load_session(session_id, config=self.config)
        if session.workflow != self.id:
            raise ValidationError("Session belongs to another workflow")
        response = session._instruction_response()
        if session.status == "active":
            try:
                self._check_session(session)
            except (ValueError, WorkflowError) as exc:
                response.pop("instruction", None)
                response["message"] = f"Incompatible stored state: {exc}"
        return response


def summary(text: str | None) -> str:
    """Return the first paragraph on one line."""
    return text.strip().split("\n\n", 1)[0].replace("\n", " ") if text else ""


def validation_message(exc: PydanticValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error['loc'])) or 'input'}: {error['msg']}"
        for error in exc.errors()
    )


# Exclude underscores to avoid collisions with fixed MCP tool names.
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def _workflow_ids(config: HirakuConfig) -> list[str]:
    """Return the IDs of workflow directories, without importing them.

    Find directories with either workflow file, including incomplete definitions.
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

    A Python sidecar exports WORKFLOW, a Workflow subclass. Without a sidecar,
    load MarkdownWorkflow and infer its inputs from front-matter defaults.
    Configuration binds the definition to the selected project's config file.

    Args:
        workflow_id: Workflow directory name.
        config: Project configuration; discovered when omitted.

    Raises:
        hiraku.errors.WorkflowError: If the ID is invalid, a sidecar
            cannot be imported, or ``start``, an event, or the states
            are invalid.
        hiraku.errors.RecordNotFoundError: If the workflow has no workflow.md.
        hiraku.errors.ValidationError: If the record is invalid or its ID does
            not match the workflow directory.

    Note:
        Importing a workflow executes trusted project Python code.
    """
    selected = config or HirakuConfig.discover()
    if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id):
        raise WorkflowError(
            f"Invalid workflow ID {workflow_id!r}: use letters, digits and '-', "
            "starting with a letter or digit"
        )
    directory = selected.workflows_dir / workflow_id
    if not directory.is_dir():
        available = ", ".join(_workflow_ids(selected)) or "none"
        raise WorkflowError(
            f"Unknown workflow {workflow_id!r}; available workflows: {available}"
        )
    sidecar = directory / "workflow.py"
    if sidecar.is_file():
        cls = _import_workflow(directory)
    else:
        from .markdown import MarkdownWorkflow

        cls = MarkdownWorkflow
    path = directory / "workflow.md"
    try:
        if not path.is_file():
            from ..errors import RecordNotFoundError

            raise RecordNotFoundError(f"Workflow record not found: {path}")
        data, markdown = cls._parse_markdown(path)
        workflow = cls(config=selected, markdown=markdown, **data)
        workflow.path = path.absolute()
        workflow._persisted_id = workflow.id
    except PydanticValidationError as exc:
        raise ValidationError(f"Invalid workflow settings in {path}: {exc}") from exc
    if workflow.id != workflow_id:
        raise ValidationError(
            f"{path} declares id {workflow.id!r}; it must match the workflow "
            f"directory {workflow_id!r}"
        )
    return workflow


def list_workflows(*, config: HirakuConfig | None = None) -> list[Workflow]:
    """Load every project workflow that loads; log and skip the others."""
    selected = config or HirakuConfig.discover()
    loaded = []
    for workflow_id in _workflow_ids(selected):
        try:
            loaded.append(load_workflow(workflow_id, config=selected))
        except Exception as exc:
            logger.warning("Workflow %s is unavailable: %s", workflow_id, exc)
    return loaded


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
            f"{path} must export WORKFLOW, a subclass of "
            "hiraku.workflows.workflow.Workflow"
        )
    return cls


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
