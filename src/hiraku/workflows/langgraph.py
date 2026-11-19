"""Run project-authored LangGraph workflows through a small MCP interface."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import frontmatter
from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ValidationError, WorkflowError
from ..project import Project

_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_CHECKPOINTS = "langgraph-checkpoints.sqlite"


class WorkflowStart(BaseModel):
    """Common request envelope for starting a project-defined workflow."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(description="Project workflow ID.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow-specific inputs; see that workflow's input schema.",
    )


class WorkflowResume(BaseModel):
    """Common request envelope for resuming an interrupted workflow."""

    model_config = ConfigDict(extra="forbid")

    workflow_session_id: str = Field(
        description="Workflow session ID returned by workflow_start."
    )
    response: dict[str, Any] = Field(
        description="Response to the current graph interrupt."
    )
    checkpoint_id: str = Field(description="Checkpoint ID from workflow status.")


def checkpoint_database(project: Project) -> Path:
    """Return the project-root checkpoint database path."""
    return project.root / ".hiraku" / _CHECKPOINTS


def _prepare_checkpoint_storage(project: Project) -> Path:
    """Ensure the project-root checkpoint directory exists."""
    directory = project.root / ".hiraku"
    directory.mkdir(parents=True, exist_ok=True)
    return checkpoint_database(project)


def workflow_ids(*, project: Project) -> list[str]:
    """List project workflow folders that define a LangGraph workflow.py."""
    if not project.workflows_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in project.workflows_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and _WORKFLOW_ID.fullmatch(path.name)
        and (path / "workflow.py").is_file()
    )


def workflow_details(*, project: Project) -> list[dict[str, Any]]:
    """Return lightweight listing data for LangGraph workflows."""
    details = []
    for workflow_id in workflow_ids(project=project):
        path = project.workflows_dir / workflow_id / "workflow.md"
        fields = frontmatter.load(path).metadata
        detail: dict[str, Any] = {
            "id": workflow_id,
            "description": fields.get("description"),
        }
        capabilities = fields.get("required_capabilities")
        if isinstance(capabilities, list) and all(
            isinstance(item, str) for item in capabilities
        ):
            detail["required_capabilities"] = capabilities
        module = _import_graph(project.workflows_dir / workflow_id / "workflow.py")
        detail["input_schema"] = _workflow_input_schema(module, workflow_id)
        details.append(detail)
    return details


def start_workflow(
    workflow_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    project: Project,
) -> dict[str, Any]:
    """Start a LangGraph workflow and run until it completes or interrupts."""
    workflow_session_id = f"{workflow_id}:{uuid4().hex}"
    with _compiled_graph(workflow_id, project) as graph:
        return _invoke_workflow(
            graph,
            workflow_id,
            workflow_session_id,
            {
                "arguments": dict(arguments or {}),
                "run_id": workflow_session_id,
            },
        )


def resume_workflow(
    workflow_session_id: str,
    response: Mapping[str, Any],
    *,
    project: Project,
    checkpoint_id: str,
) -> dict[str, Any]:
    """Resume a paused graph with the harness's structured response."""
    lock_dir = project.root / ".hiraku" / "locks"
    _prepare_checkpoint_storage(project)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(workflow_session_id.encode()).hexdigest()
    with FileLock(str(lock_dir / f"{lock_name}.lock")):
        return _resume_locked(workflow_session_id, response, project, checkpoint_id)


def _resume_locked(
    workflow_session_id: str,
    response: Mapping[str, Any],
    project: Project,
    checkpoint_id: str,
) -> dict[str, Any]:
    graph_workflow = _session_workflow_id(workflow_session_id, project)
    with _compiled_graph(graph_workflow, project) as graph:
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        if not snapshot.values:
            raise ValidationError(
                f"LangGraph workflow session {workflow_session_id!r} was not found"
            )
        if not snapshot.next:
            raise ValidationError(
                f"Workflow session {workflow_session_id!r} is not waiting for input"
            )
        current_id = snapshot.config.get("configurable", {}).get("checkpoint_id")
        if not checkpoint_id or checkpoint_id != current_id:
            raise ValidationError("Workflow checkpoint has changed; inspect its status")
        return _invoke_workflow(
            graph,
            graph_workflow,
            workflow_session_id,
            Command(resume=dict(response)),
        )


def workflow_status(workflow_session_id: str, *, project: Project) -> dict[str, Any]:
    """Inspect a checkpointed graph run without advancing it."""
    graph_workflow = _session_workflow_id(workflow_session_id, project)
    with _compiled_graph(graph_workflow, project) as graph:
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        if not snapshot.values:
            raise ValidationError(
                f"LangGraph workflow session {workflow_session_id!r} was not found"
            )
        return _snapshot_result(graph_workflow, workflow_session_id, snapshot)


def workflow_mermaid(workflow_id: str, *, project: Project) -> str:
    """Render a workflow's compiled LangGraph topology as Mermaid text."""
    with _compiled_graph(workflow_id, project) as graph:
        return graph.get_graph().draw_mermaid()


def _session_workflow_id(workflow_session_id: str, project: Project) -> str:
    """Resolve the workflow encoded in a current-format session ID."""
    workflow_id, separator, checkpoint_id = workflow_session_id.partition(":")
    if (
        not separator
        or not _WORKFLOW_ID.fullmatch(workflow_id)
        or not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id)
        or workflow_id not in workflow_ids(project=project)
    ):
        raise ValidationError(
            f"LangGraph workflow session {workflow_session_id!r} was not found"
        )
    return workflow_id


@contextmanager
def _compiled_graph(workflow_id: str, project: Project) -> Iterator[Any]:
    available_workflows = workflow_ids(project=project)
    if workflow_id not in available_workflows:
        available = ", ".join(available_workflows) or "none"
        raise WorkflowError(
            f"Unknown LangGraph workflow {workflow_id!r}; available: {available}"
        )
    directory = project.workflows_dir / workflow_id
    module = _import_graph(directory / "workflow.py")
    _workflow_input_schema(module, workflow_id)
    factory = getattr(module, "build_graph", None)
    if not callable(factory):
        raise WorkflowError(f"{directory / 'workflow.py'} must export build_graph")
    database = _prepare_checkpoint_storage(project)
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        try:
            graph = factory(checkpointer, project=project)
        except Exception as exc:
            raise WorkflowError(
                f"Cannot build LangGraph workflow {workflow_id!r}: {exc}"
            ) from exc
        if not callable(getattr(graph, "invoke", None)) or not callable(
            getattr(graph, "get_state", None)
        ):
            raise WorkflowError(
                f"{directory / 'workflow.py'} build_graph must return a compiled graph"
            )
        yield graph


def _workflow_input_schema(module: ModuleType, workflow_id: str) -> dict[str, Any]:
    factory = getattr(module, "input_schema", None)
    if not callable(factory):
        raise WorkflowError(
            f"Workflow {workflow_id!r} must export input_schema() to declare its "
            "start-input JSON Schema"
        )
    try:
        schema = factory()
    except Exception as exc:
        raise WorkflowError(
            f"Cannot load input schema for workflow {workflow_id!r}: {exc}"
        ) from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise WorkflowError(
            f"Workflow {workflow_id!r} input_schema() must return a JSON Schema "
            "object with type 'object'"
        )
    return schema


def _import_graph(path: Path) -> ModuleType:
    # Reload on each operation so edits to project workflow files take effect.
    digest = hashlib.sha256(str(path.parent.parent.resolve()).encode()).hexdigest()
    name = f"_hiraku_langgraph_{digest[:16]}_{path.parent.name}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise WorkflowError(f"Cannot import LangGraph workflow {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise WorkflowError(
            f"Cannot import LangGraph workflow {path}: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def _thread_config(workflow_session_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": workflow_session_id}}


def _invoke_workflow(
    graph: Any,
    workflow_id: str,
    workflow_session_id: str,
    invocation: Any,
) -> dict[str, Any]:
    graph.invoke(invocation, _thread_config(workflow_session_id))
    snapshot = graph.get_state(_thread_config(workflow_session_id))
    return _snapshot_result(workflow_id, workflow_session_id, snapshot)


def _snapshot_result(
    workflow_id: str, workflow_session_id: str, snapshot: Any
) -> dict[str, Any]:
    interrupts = [
        {"id": item.id, "value": item.value}
        for task in snapshot.tasks
        for item in task.interrupts
    ]
    completed = not snapshot.next and not interrupts
    result = {
        "workflow_session_id": workflow_session_id,
        "workflow_id": workflow_id,
        "status": "completed" if completed else "waiting",
        "interrupts": interrupts,
        "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id"),
    }
    if completed:
        result["result"] = snapshot.values.get("final")
    return result
