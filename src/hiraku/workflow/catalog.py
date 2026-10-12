"""Discovery and loading of project workflows."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from ..config import HirakuConfig
from ..errors import HirakuError, WorkflowError
from .core import (
    Handler,
    PromptWorkflow,
    Session,
    Workflow,
    WorkflowPrompt,
    WorkflowResource,
    handlers,
    load_workflow,
    prompts,
    resources,
    session_model,
)
from .prompts import PromptLibrary

logger = logging.getLogger(__name__)

_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MAX_TOOL_NAME = 64


@dataclass
class WorkflowEntry:
    """One discovered workflow and the result of loading it.

    Attributes:
        id: Workflow ID, taken from its directory name.
        directory: Workflow directory.
        cls: Loaded workflow class, or ``None`` if loading failed.
        definition: The ``workflow.md`` record loaded with ``cls``.
        handlers: Registered handlers by method name.
        session_model: Session model of the continuation handlers.
        description: Workflow docstring.
        error: Loading error, if any.
    """

    id: str
    directory: Path
    cls: type[Workflow] | None = None
    definition: Workflow | None = None
    handlers: dict[str, Handler] = field(default_factory=dict)
    resources: dict[str, WorkflowResource] = field(default_factory=dict)
    prompts: dict[str, WorkflowPrompt] = field(default_factory=dict)
    session_model: type[Session] | None = None
    description: str = ""
    error: str | None = None

    @property
    def loaded(self) -> bool:
        """Return whether the workflow loaded successfully."""
        return self.cls is not None and self.error is None

    def tool_name(self, handler: str) -> str:
        """Return the MCP tool name of a handler."""
        return f"{self.id}_{handler}"


class Catalog:
    """Workflows and prompt documents of a project, loaded once.

    Python modules and every ``workflow.md`` are loaded together when the
    catalog is created. A directory with only ``workflow.md`` becomes a
    `PromptWorkflow` that sends the whole document as one instruction. Later
    edits on disk take effect only when a new catalog is created, for example
    when the MCP server is reconnected.

    Args:
        config: Project configuration.

    Note:
        Importing workflows executes trusted project Python code.
    """

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.entries: dict[str, WorkflowEntry] = {}
        self._tools: dict[str, tuple[WorkflowEntry, Handler]] = {}
        root = config.workflows_dir
        directories = (
            sorted(path for path in root.iterdir() if path.is_dir())
            if root.is_dir()
            else []
        )
        documents: dict[str, Workflow] = {}
        for directory in directories:
            if directory.name.startswith((".", "_")):
                continue
            if not any(
                (directory / name).is_file() for name in ("workflow.py", "workflow.md")
            ):
                continue
            entry = WorkflowEntry(id=directory.name, directory=directory)
            self.entries[entry.id] = entry
            if not _WORKFLOW_ID.fullmatch(entry.id):
                entry.error = (
                    f"Invalid workflow ID {entry.id!r}: use letters, digits, '-' "
                    "and '_', starting with a letter or digit"
                )
                continue
            try:
                # Read with the base class, so prompts are available even when
                # the workflow's Python cannot be imported.
                documents[entry.id] = load_workflow(entry.id, config=config)
            except HirakuError as exc:
                entry.error = str(exc)
        self.prompts = PromptLibrary(documents)
        package = _root_package(config)
        for entry in self.entries.values():
            if entry.error is None:
                self._load(entry, package)
        self._register_tools()

    def _load(self, entry: WorkflowEntry, package: str) -> None:
        try:
            if (entry.directory / "workflow.py").is_file():
                module = _import_workflow(entry, package)
                cls = getattr(module, "WORKFLOW", None)
                if not (isinstance(cls, type) and issubclass(cls, Workflow)):
                    raise WorkflowError(
                        f"{entry.directory / 'workflow.py'} must export WORKFLOW, "
                        "a subclass of hiraku.workflow.Workflow"
                    )
            else:
                cls = PromptWorkflow
            entry.handlers = handlers(cls)
            entry.resources = resources(cls)
            entry.prompts = prompts(cls)
            entry.definition = load_workflow(entry.id, config=self.config, cls=cls)
            entry.session_model = session_model(cls)
            described = entry.definition.to_dict().get("description")
            entry.description = str(described or cls.__doc__ or "").strip()
            for name in [*entry.handlers, *entry.prompts]:
                tool_name = entry.tool_name(name)
                if len(tool_name) > _MAX_TOOL_NAME:
                    raise WorkflowError(
                        f"Tool name {tool_name!r} exceeds {_MAX_TOOL_NAME} characters"
                    )
            entry.cls = cls
        except Exception as exc:
            logger.exception("Could not load workflow %s", entry.id)
            entry.error = f"{type(exc).__name__}: {exc}"
            entry.handlers = {}
            entry.resources = {}
            entry.prompts = {}
            entry.cls = None
            entry.definition = None

    def _register_tools(self) -> None:
        owners: dict[str, list[tuple[WorkflowEntry, Handler]]] = {}
        for entry in self.entries.values():
            for handler in entry.handlers.values():
                owners.setdefault(entry.tool_name(handler.name), []).append(
                    (entry, handler)
                )
        for name, registrations in owners.items():
            if len(registrations) > 1:
                ids = ", ".join(sorted(entry.id for entry, _ in registrations))
                for entry, _ in registrations:
                    entry.error = f"Tool name {name!r} is defined by workflows {ids}"
        for name, registrations in owners.items():
            entry, handler = registrations[0]
            if entry.loaded:
                self._tools[name] = (entry, handler)

    def entry(self, workflow_id: str) -> WorkflowEntry:
        """Return a discovered workflow.

        Raises:
            hiraku.errors.WorkflowError: If the workflow does not exist.
        """
        try:
            return self.entries[workflow_id]
        except KeyError as exc:
            available = ", ".join(self.entries) or "none"
            raise WorkflowError(
                f"Unknown workflow {workflow_id!r}; available workflows: {available}"
            ) from exc

    def loaded_entry(self, workflow_id: str) -> WorkflowEntry:
        """Return a workflow that loaded successfully.

        Raises:
            hiraku.errors.WorkflowError: If it is unknown or failed to load.
        """
        entry = self.entry(workflow_id)
        if not entry.loaded:
            raise WorkflowError(
                f"Workflow {workflow_id!r} failed to load: {entry.error}"
            )
        return entry

    def tool(self, name: str) -> tuple[WorkflowEntry, Handler]:
        """Return the workflow and handler registered under an MCP tool name.

        Raises:
            hiraku.errors.WorkflowError: If no loaded workflow defines the tool.
        """
        try:
            return self._tools[name]
        except KeyError as exc:
            raise WorkflowError(f"Unknown workflow tool {name!r}") from exc

    def tools(self) -> list[tuple[str, WorkflowEntry, Handler]]:
        """Return all registered workflow tools in deterministic order."""
        return [
            (name, entry, handler)
            for name, (entry, handler) in sorted(self._tools.items())
        ]

    def instantiate(
        self, workflow_id: str, config: HirakuConfig | None = None
    ) -> Workflow:
        """Create a workflow instance bound to the project and its prompts.

        Args:
            workflow_id: Loaded workflow to instantiate.
            config: Current project configuration; defaults to the catalog's.
        """
        entry = self.loaded_entry(workflow_id)
        assert entry.definition is not None
        return entry.definition.bind(config or self.config, self.prompts)


def _root_package(config: HirakuConfig) -> str:
    digest = hashlib.sha256(str(config.workflows_dir.resolve()).encode()).hexdigest()
    name = f"_hiraku_workflows_{digest[:16]}"
    for module in [key for key in sys.modules if key.split(".")[0] == name]:
        del sys.modules[module]
    spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
    spec.submodule_search_locations = [str(config.workflows_dir)]
    sys.modules[name] = importlib.util.module_from_spec(spec)
    return name


def _import_workflow(entry: WorkflowEntry, root: str) -> ModuleType:
    # Each workflow directory is a package below a project-specific root
    # package, so relative imports work without changing sys.path.
    package_name = f"{root}.{entry.id}"
    package_spec = importlib.machinery.ModuleSpec(package_name, None, is_package=True)
    package_spec.submodule_search_locations = [str(entry.directory)]
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package
    module_name = f"{package_name}.workflow"
    spec = importlib.util.spec_from_file_location(
        module_name, entry.directory / "workflow.py"
    )
    if spec is None or spec.loader is None:
        raise WorkflowError(f"Cannot import {entry.directory / 'workflow.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module
