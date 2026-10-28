"""Run project-authored LangGraph workflows through a small MCP interface."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..base import BaseRecord
from ..config import HirakuConfig
from ..errors import ValidationError, WorkflowError

logger = logging.getLogger(__name__)
_CHECKPOINTS = "langgraph-checkpoints.sqlite"
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
        fields, _ = BaseRecord._parse_markdown(path)
        details.append({"id": workflow_id, "description": fields.get("description")})
    return details


def start_workflow(
    workflow_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    config: HirakuConfig,
) -> dict[str, Any]:
    """Start a LangGraph workflow and run until it completes or interrupts."""
    workflow_session_id = f"{workflow_id}:{uuid4().hex}"
    with _compiled_graph(workflow_id, config) as graph:
        graph.invoke(
            {
                "arguments": dict(arguments or {}),
                "run_id": workflow_session_id,
            },
            _thread_config(workflow_session_id),
        )
        return _snapshot_result(graph, workflow_id, workflow_session_id)


def resume_workflow(
    workflow_session_id: str,
    response: Mapping[str, Any],
    *,
    config: HirakuConfig,
) -> dict[str, Any]:
    """Resume a paused graph with the harness's structured response."""
    graph_workflow = _find_run(workflow_session_id, config)
    if graph_workflow is None:
        raise ValidationError(
            f"LangGraph workflow session {workflow_session_id!r} was not found"
        )
    with _compiled_graph(graph_workflow, config) as graph:
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        if not snapshot.values or not snapshot.next:
            raise ValidationError(
                f"Workflow session {workflow_session_id!r} is not waiting for input"
            )
        graph.invoke(
            Command(resume=dict(response)), _thread_config(workflow_session_id)
        )
        return _snapshot_result(graph, graph_workflow, workflow_session_id)


def workflow_status(
    workflow_session_id: str, *, config: HirakuConfig
) -> dict[str, Any]:
    """Inspect a checkpointed graph run without advancing it."""
    graph_workflow = _find_run(workflow_session_id, config)
    if graph_workflow is None:
        raise ValidationError(
            f"LangGraph workflow session {workflow_session_id!r} was not found"
        )
    with _compiled_graph(graph_workflow, config) as graph:
        return _snapshot_result(graph, graph_workflow, workflow_session_id)


def workflow_mermaid(workflow_id: str, *, config: HirakuConfig) -> str:
    """Render a workflow's compiled LangGraph topology as Mermaid text."""
    with _compiled_graph(workflow_id, config) as graph:
        return graph.get_graph().draw_mermaid()


def _find_run(workflow_session_id: str, config: HirakuConfig) -> str | None:
    workflow_id, separator, checkpoint_id = workflow_session_id.partition(":")
    if (
        separator
        and _WORKFLOW_ID.fullmatch(workflow_id)
        and re.fullmatch(r"[0-9a-f]{32}", checkpoint_id)
        and workflow_id in workflow_ids(config=config)
    ):
        try:
            with _compiled_graph(workflow_id, config) as graph:
                snapshot = graph.get_state(_thread_config(workflow_session_id))
            return workflow_id if snapshot.values else None
        except Exception as exc:
            logger.warning("LangGraph workflow %s is unavailable: %s", workflow_id, exc)
            return None

    # Older PoC runs used bare UUIDs. They are unambiguous while only one graph
    # has checkpoints; fail safely if multiple graphs claim the same thread.
    matches = []
    for candidate in workflow_ids(config=config):
        try:
            with _compiled_graph(candidate, config) as graph:
                snapshot = graph.get_state(_thread_config(workflow_session_id))
            if snapshot.values:
                matches.append(candidate)
        except Exception as exc:
            logger.warning("LangGraph workflow %s is unavailable: %s", candidate, exc)
    if len(matches) > 1:
        raise ValidationError(
            f"Workflow session {workflow_session_id!r} matches multiple graphs: "
            f"{', '.join(matches)}"
        )
    return matches[0] if matches else None


@contextmanager
def _compiled_graph(workflow_id: str, config: HirakuConfig) -> Iterator[Any]:
    if workflow_id not in workflow_ids(config=config):
        available = ", ".join(workflow_ids(config=config)) or "none"
        raise WorkflowError(
            f"Unknown LangGraph workflow {workflow_id!r}; available: {available}"
        )
    directory = config.workflows_dir / workflow_id
    module = _import_graph(directory / "workflow.py")
    factory = getattr(module, "build_graph", None)
    if not callable(factory):
        raise WorkflowError(f"{directory / 'workflow.py'} must export build_graph")
    database = config.hiraku_dir / _CHECKPOINTS
    database.parent.mkdir(parents=True, exist_ok=True)
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


def _import_graph(path: Path) -> ModuleType:
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


def _snapshot_result(
    graph: Any, workflow_id: str, workflow_session_id: str
) -> dict[str, Any]:
    snapshot = graph.get_state(_thread_config(workflow_session_id))
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
    }
    if completed:
        result["result"] = snapshot.values.get("final")
    return result
