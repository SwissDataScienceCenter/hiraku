"""Workflow definitions, instructions, and project-level loading."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import logging
import re
import sys
import typing
from pathlib import Path
from typing import Any, Literal, Self

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
from ..context import load_context
from ..errors import ValidationError, WorkflowError
from ..record import load_record
from .graph import Event, EventArguments, State, WorkflowGraph

logger = logging.getLogger(__name__)


class Instruction(BaseModel):
    """An assignment, inline data, research references, and accepted events."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1)
    data: Any = None
    context: dict[Literal["contexts", "records"], list[str]] | None = None
    events: dict[str, Any] | None = None

    def __init__(
        self,
        text: str,
        *,
        data: Any = None,
        context: dict[Literal["contexts", "records"], list[str]] | None = None,
        **values: Any,
    ) -> None:
        super().__init__(
            text=text.strip() if isinstance(text, str) else text,
            data=data,
            context=context,
            **values,
        )

    def to_text(self) -> str:
        """Render text, data, research references, and accepted events."""
        parts = [self.text]
        fence = chr(96) * 3
        for label, value in (
            ("Data", self.data),
            ("Context", self.context),
            ("Events", self.events),
        ):
            if value is not None:
                rendered = json.dumps(to_jsonable_python(value), indent=1)
                parts.append(f"{label}:\n{fence}json\n{rendered}\n{fence}")
        return "\n\n".join(parts)


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
        """Compile decorators and validate Workflow-specific handler contracts.

        Subclasses may construct a graph explicitly instead; execution still
        validates returned states and instructions.
        """
        graph = WorkflowGraph.from_class(type(self))
        reserved = set(dir(Workflow))
        for item in (*graph.events.values(), *graph.nodes.values()):
            if item.handler.__name__ in reserved:
                raise WorkflowError(
                    f"Invalid workflow handler: {item.handler.__name__}"
                )
        for node in graph.nodes.values():
            if typing.get_type_hints(node.handler).get("return") is not Instruction:
                raise WorkflowError(
                    f"Node handler {node.handler.__name__} must return Instruction"
                )
        return graph

    def run(
        self,
        event: Event,
        arguments: EventArguments,
        state: State | None = None,
    ) -> tuple[State, Instruction]:
        """Execute one step using prepared inputs, without session persistence.

        Arguments and incoming state must already be validated against the graph.
        Omit state for the start event. Handler and validation errors propagate.
        Event handlers may mutate the supplied state.
        """
        parameters = [] if state is None else [state]
        inputs = {
            name: getattr(arguments, name) for name in type(arguments).model_fields
        }
        returned = event.handler(self, *parameters, **inputs)
        self.graph.check_transition(event, returned)
        # Revalidate assignments and copy updates before using the returned state.
        raw = returned.model_dump(mode="json", warnings=False)
        json.dumps(raw, allow_nan=False)
        state = self.graph.state_model.model_validate(raw)
        # Model validators can also change the selected node.
        self.graph.check_transition(event, state)
        node = self.graph.nodes[state.node]
        instruction = node.handler(self, state.model_copy(deep=True))
        if not isinstance(instruction, Instruction):
            raise WorkflowError(f"Node handler {state.node!r} must return Instruction")
        if instruction.events is not None:
            raise WorkflowError("State instructions cannot override graph events")
        events = self.graph.events_from(state.node)
        instruction = instruction.model_copy(
            update={
                "data": to_jsonable_python(instruction.data),
                "events": {
                    "accepted": [
                        {
                            "event": item.name,
                            "description": summary(item.description),
                            "arguments": item.arguments.model_json_schema(),
                        }
                        for item in events
                    ]
                }
                if events
                else None,
            }
        )
        raw = instruction.model_dump(mode="json")
        json.dumps(raw, allow_nan=False)
        instruction = Instruction.model_validate(raw)
        if instruction.context:
            config = self.config
            for key, loader in (("contexts", load_context), ("records", load_record)):
                identifiers = instruction.context.get(key, [])
                identifiers[:] = dict.fromkeys(identifiers)
                for identifier in identifiers:
                    loader(identifier, config=config)
        return state, instruction

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
    if workflow_id == "runs":
        raise WorkflowError(
            "Workflow ID 'runs' is reserved for Hiraku's run-query tool"
        )
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
