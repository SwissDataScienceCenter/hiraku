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

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..config import HirakuConfig
from ..errors import ValidationError, WorkflowError
from ..markdown import read_markdown
from .session_history import (
    prepare_checkpoint_storage,
    record_event,
    validate_context,
)

_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def workflow_ids(*, config: HirakuConfig) -> list[str]:
    """List project workflow folders that define a LangGraph workflow.py."""
    if not config.workflows_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in config.workflows_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and _WORKFLOW_ID.fullmatch(path.name)
        and (path / "workflow.py").is_file()
    )


def workflow_details(*, config: HirakuConfig) -> list[dict[str, Any]]:
    """Return lightweight listing data for LangGraph workflows."""
    details = []
    for workflow_id in workflow_ids(config=config):
        path = config.workflows_dir / workflow_id / "workflow.md"
        fields, _ = read_markdown(path)
        detail: dict[str, Any] = {
            "id": workflow_id,
            "description": fields.get("description"),
        }
        capabilities = fields.get("required_capabilities")
        if isinstance(capabilities, list) and all(
            isinstance(item, str) for item in capabilities
        ):
            detail["required_capabilities"] = capabilities
        module = _import_graph(config.workflows_dir / workflow_id / "workflow.py")
        detail["input_schema"] = _workflow_input_schema(module, workflow_id)
        details.append(detail)
    return details


def start_workflow(
    workflow_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    config: HirakuConfig,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a LangGraph workflow and run until it completes or interrupts."""
    validate_context(context)
    workflow_session_id = f"{workflow_id}:{uuid4().hex}"
    with _compiled_graph(workflow_id, config) as graph:
        record_event(
            workflow_session_id,
            workflow_id,
            "started",
            "running",
            config=config,
            context=context,
        )
        return _invoke_and_record(
            graph,
            workflow_id,
            workflow_session_id,
            {
                "arguments": dict(arguments or {}),
                "run_id": workflow_session_id,
            },
            config,
            context,
        )


def resume_workflow(
    workflow_session_id: str,
    response: Mapping[str, Any],
    *,
    config: HirakuConfig,
    checkpoint_id: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a paused graph with the harness's structured response."""
    validate_context(context)
    lock_dir = config.hiraku_dir / ".hiraku" / "locks"
    prepare_checkpoint_storage(config)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(workflow_session_id.encode()).hexdigest()
    with FileLock(str(lock_dir / f"{lock_name}.lock")):
        return _resume_locked(
            workflow_session_id, response, config, checkpoint_id, context
        )


def _resume_locked(
    workflow_session_id: str,
    response: Mapping[str, Any],
    config: HirakuConfig,
    checkpoint_id: str,
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    graph_workflow = _session_workflow_id(workflow_session_id, config)
    with _compiled_graph(graph_workflow, config) as graph:
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
        record_event(
            workflow_session_id,
            graph_workflow,
            "resume_requested",
            "running",
            config=config,
            context=context,
            checkpoint_id=snapshot.config.get("configurable", {}).get("checkpoint_id"),
        )
        return _invoke_and_record(
            graph,
            graph_workflow,
            workflow_session_id,
            Command(resume=dict(response)),
            config,
            context,
        )


def workflow_status(
    workflow_session_id: str, *, config: HirakuConfig
) -> dict[str, Any]:
    """Inspect a checkpointed graph run without advancing it."""
    graph_workflow = _session_workflow_id(workflow_session_id, config)
    with _compiled_graph(graph_workflow, config) as graph:
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        if not snapshot.values:
            raise ValidationError(
                f"LangGraph workflow session {workflow_session_id!r} was not found"
            )
        return _snapshot_result(graph_workflow, workflow_session_id, snapshot)


def workflow_mermaid(workflow_id: str, *, config: HirakuConfig) -> str:
    """Render a workflow's compiled LangGraph topology as Mermaid text."""
    with _compiled_graph(workflow_id, config) as graph:
        return graph.get_graph().draw_mermaid()


def _session_workflow_id(workflow_session_id: str, config: HirakuConfig) -> str:
    """Resolve the workflow encoded in a current-format session ID."""
    workflow_id, separator, checkpoint_id = workflow_session_id.partition(":")
    if (
        not separator
        or not _WORKFLOW_ID.fullmatch(workflow_id)
        or not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id)
        or workflow_id not in workflow_ids(config=config)
    ):
        raise ValidationError(
            f"LangGraph workflow session {workflow_session_id!r} was not found"
        )
    return workflow_id


@contextmanager
def _compiled_graph(workflow_id: str, config: HirakuConfig) -> Iterator[Any]:
    available_workflows = workflow_ids(config=config)
    if workflow_id not in available_workflows:
        available = ", ".join(available_workflows) or "none"
        raise WorkflowError(
            f"Unknown LangGraph workflow {workflow_id!r}; available: {available}"
        )
    directory = config.workflows_dir / workflow_id
    module = _import_graph(directory / "workflow.py")
    _workflow_input_schema(module, workflow_id)
    factory = getattr(module, "build_graph", None)
    if not callable(factory):
        raise WorkflowError(f"{directory / 'workflow.py'} must export build_graph")
    database = prepare_checkpoint_storage(config)
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        try:
            graph = factory(checkpointer, config=config)
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


def _invoke_and_record(
    graph: Any,
    workflow_id: str,
    workflow_session_id: str,
    invocation: Any,
    config: HirakuConfig,
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    try:
        graph.invoke(invocation, _thread_config(workflow_session_id))
    except Exception as exc:
        record_event(
            workflow_session_id,
            workflow_id,
            "failed",
            "failed",
            config=config,
            context=context,
            error_type=type(exc).__name__,
        )
        raise
    return _record_snapshot(graph, workflow_id, workflow_session_id, config)


def _record_snapshot(
    graph: Any, workflow_id: str, workflow_session_id: str, config: HirakuConfig
) -> dict[str, Any]:
    snapshot = graph.get_state(_thread_config(workflow_session_id))
    result = _snapshot_result(workflow_id, workflow_session_id, snapshot)
    checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id")
    record_event(
        workflow_session_id,
        workflow_id,
        result["status"],
        result["status"],
        config=config,
        checkpoint_id=checkpoint_id,
    )
    return result


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
