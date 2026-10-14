"""Workflows: a ``workflow.md`` record whose ``start`` and replies run on sessions."""

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
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeGuard, TypeVar

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
    from .config import HirakuConfig

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])
_REPLY_MARKER = "__hiraku_reply__"
# No underscores, so the tool named after a workflow cannot collide with fixed
# tool names such as workflow_reply.
_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def reply(function: _F) -> _F:
    """Mark a workflow method as a reply that continues a session.

    The first parameter after ``self`` is the session, annotated with the
    workflow's `Session` subclass. The other parameters and the docstring define
    the reply's arguments and description. A reply is accepted only while the
    session's pending instruction lists it, e.g. ``replies=[self.approve]``.

    The decorated method stays directly callable from Python.
    """
    setattr(function, _REPLY_MARKER, True)
    return function


@dataclass(frozen=True)
class Reply:
    """A `reply` method of a workflow and its derived argument model."""

    name: str
    function: Callable[..., Any]
    arguments: type[BaseModel]
    session_model: type[Session]
    description: str


class Workflow(BaseRecord):
    """A workflow: its ``workflow.md`` record, ``start``, and its replies.

    Subclass it in ``hiraku/workflows/<workflow-id>/workflow.py`` and export the
    class as ``WORKFLOW``. The subclass defines ``start``, whose signature and
    docstring define how the workflow is started and which returns the first
    instruction of a new session, and `reply` methods that continue a session.

    An instance is the loaded ``workflow.md``: the front matter holds the
    workflow ``id``, which must match the directory name, and any settings the
    subclass declares as annotated fields; the Markdown body holds orientation
    and prompt sections. The base class alone loads any ``workflow.md`` without
    importing project code. The runtime loads each workflow once and gives
    every call a fresh copy bound to the project; changes to an instance are
    not persisted.

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

    def start_arguments(self) -> type[BaseModel]:
        """Return the model of the arguments of ``start``.

        It is derived from the signature of ``start``; `PromptWorkflow`
        overrides it with the arguments declared in ``workflow.md``.
        """
        return start_model(type(self))

    def run_start(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Start a session: call ``start`` and store the session it returns.

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
        response, failure = _invoke(
            self, "start", lambda: start(**_keywords(validated))
        )
        if failure is not None:
            return failure
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
            "orientation": self.orientation,
            **session.response,
        }

    def run_reply(
        self,
        session_id: str,
        instruction_id: int,
        reply: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Answer the pending instruction of a stored session with a reply.

        The session is locked while the reply runs, and saved only if the reply
        is accepted. The workflow must be bound to a project with `bind`.

        Args:
            session_id: Session to continue.
            instruction_id: The instruction the reply answers.
            reply: Name of a `reply` method listed by that instruction.
            arguments: JSON-compatible arguments of the reply.

        Returns:
            A JSON-compatible response whose ``status`` is ``accepted``,
            ``rejected``, ``not_ready``, ``stale``, or ``error``, with the
            session's current instruction or result.

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
                stored = Session.load(session_id, config=self.config)
                if stored.workflow != self.id:
                    return stored.outcome(
                        "rejected",
                        f"Session {session_id!r} belongs to workflow "
                        f"{stored.workflow!r}, not {self.id!r}",
                    )
                method = reply_methods(type(self)).get(reply)
                if method is None:
                    return stored.outcome(
                        "rejected",
                        f"{reply!r} is not a reply of workflow {self.id!r}; the "
                        "pending instruction accepts: "
                        f"{', '.join(stored.replies) or 'none'}",
                    )
                validated, failure = _validate(method.arguments, arguments)
                if failure is not None:
                    return {**failure, **stored.response}
                values = validated.model_dump(mode="json")
                early = stored.check(reply, instruction_id, values)
                if early is not None:
                    return early
                try:
                    session = stored.typed(method.session_model)
                except ValidationError as exc:
                    return stored.outcome("rejected", _incompatible(exc))
                response, failure = _invoke(
                    self,
                    reply,
                    lambda: method.function(self, session, **_keywords(validated)),
                )
                if failure is not None:
                    return {**failure, **stored.response}
                try:
                    result = _checked_response(self, response)
                except WorkflowError as exc:
                    return stored.outcome("error", str(exc))
                # The runtime owns the base fields; discard any changes the reply
                # made.
                for name in Session.model_fields:
                    setattr(result, name, getattr(stored, name))
                call = HistoryEntry(
                    instruction_id=instruction_id,
                    reply=reply,
                    arguments=values,
                    at=datetime.now(UTC),
                )
                result.advance(response, _reply_specs(self), call)
                result.save(config=self.config)
                return {"status": "accepted", **result.response}
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
            replies: `reply` methods of this workflow, e.g.
                ``[self.select, self.stop]``.
            context: Concise JSON-compatible data.
            requires_user_response: Whether a user answer is required before
                replying.

        Raises:
            hiraku.errors.WorkflowError: If a reply is not a `reply` method of
                this workflow or the context is not JSON-compatible.
        """
        if not isinstance(session, Session):
            raise WorkflowError("Instructions require a Session instance")
        if not text or not text.strip():
            raise WorkflowError("Instruction text must not be empty")
        if not replies:
            raise WorkflowError("An instruction must offer at least one reply")
        names = tuple(self._reply_name(method) for method in replies)
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

    def _reply_name(self, method: Callable[..., Any]) -> str:
        function = getattr(method, "__func__", None)
        name = getattr(function, "__name__", "")
        registered = reply_methods(type(self)).get(name)
        if (
            getattr(method, "__self__", None) is not self
            or registered is None
            or registered.function is not function
        ):
            raise WorkflowError(
                f"Replies must be @reply methods of this workflow, e.g. "
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

    def start(self, **arguments: Any) -> Instruction:
        """Start the workflow; its whole prompt arrives as one instruction."""
        return self.instruction(
            Session(),
            render(self.text, arguments),
            context=arguments or None,
            replies=[self.complete],
        )

    @reply
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
            imported, or ``start`` or a reply is invalid.
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
    reply_methods(cls)
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
def reply_methods(cls: type[Workflow]) -> dict[str, Reply]:
    """Collect and validate the `reply` methods of a workflow class.

    Raises:
        hiraku.errors.WorkflowError: If a reply's signature is unsupported, its
            name is taken by `Workflow`, or replies use different session
            models.
    """
    found: dict[str, Reply] = {}
    for name in sorted(dir(cls)):
        function = inspect.getattr_static(cls, name, None)
        if not getattr(function, _REPLY_MARKER, False):
            continue
        label = f"{cls.__name__}.{name}"
        if name == "start" or hasattr(Workflow, name):
            raise WorkflowError(
                f"@reply {label} is named like a Workflow method; rename it"
            )
        if not inspect.isfunction(function):
            raise WorkflowError(f"@reply {label} must be a method")
        parameters = _parameters(label, function)
        hints = _hints(label, function)
        first = parameters[0] if parameters else None
        model = hints.get(first.name) if first is not None else None
        if (
            first is None
            or first.kind is inspect.Parameter.KEYWORD_ONLY
            or not _is_session(model)
        ):
            raise WorkflowError(
                f"@reply {label} must take the session as its first parameter, "
                "annotated with a Session subclass"
            )
        found[name] = Reply(
            name=name,
            function=function,
            arguments=_arguments_model(label, parameters[1:], hints),
            session_model=model,
            description=inspect.getdoc(function) or "",
        )
    models = {item.session_model for item in found.values()}
    if len(models) > 1:
        names = ", ".join(sorted(model.__name__ for model in models))
        raise WorkflowError(
            f"Replies of {cls.__name__} use different session models: {names}"
        )
    return found


def session_model(cls: type[Workflow]) -> type[Session] | None:
    """Return the session model shared by a workflow's replies."""
    return next((item.session_model for item in reply_methods(cls).values()), None)


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
    """Derive the argument model of ``start`` or a reply from its parameters."""
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
        annotation = hints[parameter.name]
        if _is_session(annotation):
            raise WorkflowError(
                f"{label} parameter {parameter.name!r} cannot be a session; only "
                "the first parameter of a @reply method is"
            )
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (annotation, default)
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


def _is_session(annotation: Any) -> TypeGuard[type[Session]]:
    return isinstance(annotation, type) and issubclass(annotation, Session)


# Running start and replies ------------------------------------------------------


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
    """Call ``start`` or a reply; return its result, or a failure status."""
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


def _checked_response(workflow: Workflow, response: Any) -> Session:
    if not isinstance(response, Instruction | Completion):
        raise WorkflowError(
            f"Workflow {workflow.id!r} returned {type(response).__name__}; start "
            "and replies must return self.instruction(...) or "
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
    registered = reply_methods(type(workflow))
    missing = [name for name in session.replies if name not in registered]
    if missing:
        return (
            "The loaded workflow no longer defines the pending replies "
            f"{', '.join(missing)}. Inspect the session and start a new one."
        )
    model = session_model(type(workflow))
    if model is None:
        return "The loaded workflow defines no replies."
    try:
        session.typed(model)
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
            "description": summary(method.description),
            "arguments": _compact_schema(method.arguments),
        }
        for name, method in reply_methods(type(workflow)).items()
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
    "reply",
]
