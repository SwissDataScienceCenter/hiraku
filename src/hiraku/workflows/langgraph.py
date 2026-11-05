"""Run project-defined LangGraph workflows with durable checkpoints."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from filelock import FileLock
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..errors import ValidationError, WorkflowError
from ..project import Project
from ..record import Record
from .session_history import (
    prepare_checkpoint_storage,
    record_event,
    validate_context,
)

_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_SESSION_ID = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):([0-9a-f]{32})$")
_ACTIONS = frozenset({"task", "review", "approval"})


def workflow_ids(*, project: Project) -> list[str]:
    """Discover project workflow directories containing a Python graph factory."""
    root = project.workflows_dir
    if not root.is_dir():
        return []
    return sorted(
        directory.name
        for directory in root.iterdir()
        if directory.is_dir()
        and not directory.is_symlink()
        and _WORKFLOW_ID.fullmatch(directory.name)
        and (directory / "workflow.py").is_file()
    )


def workflow_details(*, project: Project) -> list[dict[str, Any]]:
    """List workflows without compiling or executing graphs."""
    details: list[dict[str, Any]] = []
    for workflow_id in workflow_ids(project=project):
        directory = project.workflows_dir / workflow_id
        markdown_path = directory / "workflow.md"
        fields: dict[str, Any] = {}
        if markdown_path.is_file():
            fields, _ = Record._parse_markdown(markdown_path)
        module = _import_graph(directory / "workflow.py")
        detail: dict[str, Any] = {
            "id": workflow_id,
            "description": fields.get("description"),
            "input_schema": _workflow_input_schema(module, workflow_id),
        }
        capabilities = fields.get("required_capabilities")
        if isinstance(capabilities, list) and all(
            isinstance(x, str) for x in capabilities
        ):
            detail["required_capabilities"] = capabilities
        details.append(detail)
    return details


def start_workflow(
    workflow_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    project: Project,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a thread and run it until completion or the first interrupt."""
    validate_context(context)
    if workflow_id not in workflow_ids(project=project):
        raise WorkflowError(f"Unknown workflow {workflow_id!r}")

    start_arguments = dict(arguments or {})
    module = _import_graph(project.workflows_dir / workflow_id / "workflow.py")
    prepare_start = getattr(module, "prepare_start_arguments", None)
    if callable(prepare_start):
        try:
            start_arguments = prepare_start(start_arguments, dict(context or {}))
        except (ValidationError, WorkflowError):
            raise
        except Exception as exc:
            raise WorkflowError(
                f"Cannot prepare arguments for workflow {workflow_id!r}: {exc}"
            ) from exc
        if not isinstance(start_arguments, Mapping):
            raise WorkflowError(
                f"Workflow {workflow_id!r} prepare_start_arguments() must "
                "return an object"
            )

    session_id = f"{workflow_id}:{uuid4().hex}"
    with _compiled_graph(workflow_id, project) as (graph, runtime_context):
        record_event(
            session_id,
            workflow_id,
            "started",
            "running",
            project=project,
            context=context,
        )
        return _invoke_and_record(
            graph,
            workflow_id,
            session_id,
            {"arguments": dict(start_arguments)},
            project=project,
            caller_context=context,
            runtime_context=runtime_context,
        )


def resume_workflow(
    workflow_session_id: str,
    response: Mapping[str, Any],
    *,
    project: Project,
    checkpoint_id: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically check and consume a checkpoint under a per-session file lock.

    This protects cooperative Hiraku processes sharing the same filesystem.
    It does not make external side effects idempotent, and the MCP adapter must
    separately enforce trusted authorization for human-owned interrupts.
    """
    validate_context(context)
    if not isinstance(response, Mapping):
        raise ValidationError("Workflow response must be an object")
    workflow_id = _session_workflow_id(workflow_session_id, project)

    with (
        _session_lock(workflow_session_id, project),
        _compiled_graph(workflow_id, project) as (graph, runtime_context),
    ):
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        _require_existing_session(snapshot, workflow_session_id)
        if not snapshot.next or not _interrupts(snapshot):
            raise ValidationError("Workflow is not waiting at an interrupt")
        current_id = _checkpoint_id(snapshot)
        if not checkpoint_id or current_id != checkpoint_id:
            raise ValidationError("Workflow checkpoint has changed; inspect its status")
        if len(_interrupts(snapshot)) != 1:
            raise ValidationError(
                "workflow_resume requires exactly one pending interrupt"
            )

        pending = _interrupts(snapshot)[0]["value"]
        validated_response = _validate_resume_response(
            workflow_id, project, pending, dict(response)
        )

        # Only the MCP adapter can attest to a human elicitation result.
        # Do not add a generic 'approve' shortcut in this runner.
        record_event(
            workflow_session_id,
            workflow_id,
            "resume_requested",
            "running",
            project=project,
            context=context,
            checkpoint_id=current_id,
        )
        return _invoke_and_record(
            graph,
            workflow_id,
            workflow_session_id,
            Command(resume=validated_response),
            project=project,
            caller_context=context,
            runtime_context=runtime_context,
        )


def workflow_status(workflow_session_id: str, *, project: Project) -> dict[str, Any]:
    """Inspect the latest checkpoint without advancing the graph."""
    workflow_id = _session_workflow_id(workflow_session_id, project)
    with (
        _session_lock(workflow_session_id, project),
        _compiled_graph(workflow_id, project) as (graph, _),
    ):
        snapshot = graph.get_state(_thread_config(workflow_session_id))
        _require_existing_session(snapshot, workflow_session_id)
        return _snapshot_result(workflow_id, workflow_session_id, snapshot)


def workflow_mermaid(workflow_id: str, *, project: Project) -> str:
    """Render the current workflow definition as Mermaid source."""
    with _compiled_graph(workflow_id, project) as (graph, _):
        return graph.get_graph().draw_mermaid()


def _session_workflow_id(session_id: str, project: Project) -> str:
    match = _SESSION_ID.fullmatch(session_id)
    if match is None or match.group(1) not in workflow_ids(project=project):
        raise ValidationError(
            f"LangGraph workflow session {session_id!r} was not found"
        )
    return match.group(1)


@contextmanager
def _session_lock(session_id: str, project: Project) -> Iterator[None]:
    # hiraku_dir is itself Hiraku's data directory: do not append '.hiraku' again.
    lock_dir = project.hiraku_dir / "locks"
    prepare_checkpoint_storage(project)
    lock_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    with FileLock(str(lock_dir / f"{name}.lock")):
        yield


@contextmanager
def _compiled_graph(
    workflow_id: str, project: Project
) -> Iterator[tuple[Any, Any | None]]:
    """Compile a graph with a checkpointer scoped to this operation.

    The graph factory supports both the legacy build_graph(checkpointer,
    project=project) and v2 build_graph(checkpointer) + build_context(project).
    """
    if workflow_id not in workflow_ids(project=project):
        raise WorkflowError(f"Unknown LangGraph workflow {workflow_id!r}")

    path = project.workflows_dir / workflow_id / "workflow.py"
    module = _import_graph(path)
    _workflow_input_schema(module, workflow_id)
    factory = getattr(module, "build_graph", None)
    if not callable(factory):
        raise WorkflowError(f"{path} must export build_graph")

    database = prepare_checkpoint_storage(project)
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        try:
            parameters = inspect.signature(factory).parameters
            graph = (
                factory(checkpointer, project=project)
                if "project" in parameters
                else factory(checkpointer)
            )
            context_factory = getattr(module, "build_context", None)
            runtime_context = (
                context_factory(project) if callable(context_factory) else None
            )
        except Exception as exc:
            raise WorkflowError(
                f"Cannot build LangGraph workflow {workflow_id!r}: {exc}"
            ) from exc
        if not callable(getattr(graph, "invoke", None)) or not callable(
            getattr(graph, "get_state", None)
        ):
            raise WorkflowError(f"{path} build_graph must return a compiled graph")
        yield graph, runtime_context


def _workflow_input_schema(module: ModuleType, workflow_id: str) -> dict[str, Any]:
    factory = getattr(module, "input_schema", None)
    if not callable(factory):
        raise WorkflowError(f"Workflow {workflow_id!r} must export input_schema()")
    try:
        schema = factory()
    except Exception as exc:
        raise WorkflowError(
            f"Cannot load input schema for workflow {workflow_id!r}: {exc}"
        ) from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise WorkflowError(
            f"Workflow {workflow_id!r} input_schema must be a JSON Schema object"
        )
    return schema


def _validate_resume_response(
    workflow_id: str,
    project: Project,
    pending: Any,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Validate against a workflow contract before LangGraph records a resume."""
    path = project.workflows_dir / workflow_id / "workflow.py"
    module = _import_graph(path)
    validator = getattr(module, "validate_resume_response", None)
    if not callable(validator):
        schema = (
            pending.get("response_schema") if isinstance(pending, Mapping) else None
        )
        if isinstance(schema, dict):
            try:
                validate_json_schema(response, schema)
            except JsonSchemaValidationError as exc:
                raise ValidationError(
                    f"Workflow response does not match response_schema: {exc.message}"
                ) from exc
        return response
    try:
        validated = validator(pending, response)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            f"Invalid response for workflow {workflow_id!r}: {exc}"
        ) from exc
    if not isinstance(validated, Mapping):
        raise WorkflowError(
            f"Workflow {workflow_id!r} validate_resume_response() must return an object"
        )
    return dict(validated)


def _import_graph(path: Path) -> ModuleType:
    """Load the current project-authored Python graph definition.

    WARNING: this preserves Hiraku's hot-reload behavior. Persisted checkpoints
    are NOT pinned to their original code version. The caller must route legacy
    sessions to compatible versions before enabling incompatible graph changes.
    """
    digest = hashlib.sha256(
        str(path.parent.parent.resolve()).encode("utf-8")
    ).hexdigest()
    name = f"_hiraku_langgraph_{digest[:16]}_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise WorkflowError(f"Cannot import LangGraph workflow {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise WorkflowError(
            f"Cannot import LangGraph workflow {path}: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def _thread_config(session_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": session_id}}


def _invoke_and_record(
    graph: Any,
    workflow_id: str,
    session_id: str,
    invocation: Any,
    *,
    project: Project,
    caller_context: Mapping[str, Any] | None,
    runtime_context: Any | None,
) -> dict[str, Any]:
    try:
        invocation_config = _thread_config(session_id)
        if runtime_context is None:
            graph.invoke(invocation, project=invocation_config)
        else:
            graph.invoke(invocation, project=invocation_config, context=runtime_context)
    except Exception as exc:
        record_event(
            session_id,
            workflow_id,
            "failed",
            "failed",
            project=project,
            context=caller_context,
            error_type=type(exc).__name__,
        )
        raise
    return _record_snapshot(graph, workflow_id, session_id, project)


def _record_snapshot(
    graph: Any, workflow_id: str, session_id: str, project: Project
) -> dict[str, Any]:
    snapshot = graph.get_state(_thread_config(session_id))
    result = _snapshot_result(workflow_id, session_id, snapshot)
    record_event(
        session_id,
        workflow_id,
        result["status"],
        result["status"],
        project=project,
        checkpoint_id=_checkpoint_id(snapshot),
    )
    return result


def _checkpoint_id(snapshot: Any) -> str | None:
    return (snapshot.project or {}).get("configurable", {}).get("checkpoint_id")


def _interrupts(snapshot: Any) -> list[dict[str, Any]]:
    return [
        {"id": item.id, "value": item.value}
        for task in snapshot.tasks
        for item in task.interrupts
    ]


def _require_existing_session(snapshot: Any, session_id: str) -> None:
    # Empty values can be legitimate in a graph. Check for an actual checkpoint.
    if _checkpoint_id(snapshot) is None:
        raise ValidationError(
            f"LangGraph workflow session {session_id!r} was not found"
        )


def _snapshot_result(
    workflow_id: str, session_id: str, snapshot: Any
) -> dict[str, Any]:
    interrupts = _interrupts(snapshot)
    # Pending tasks without interrupts are not a completed workflow.
    completed = not snapshot.next and not interrupts
    next_actions = [_next_action(item["value"]) for item in interrupts]
    result: dict[str, Any] = {
        "workflow_session_id": session_id,
        "workflow_id": workflow_id,
        "status": "completed" if completed else "waiting",
        "interrupts": interrupts,
        "next_actions": next_actions,
        "next_action": next_actions[0] if len(next_actions) == 1 else None,
        "checkpoint_id": _checkpoint_id(snapshot),
    }
    if completed:
        result["result"] = (snapshot.values or {}).get("final")
    return result


def _next_action(value: Any) -> dict[str, Any]:
    """Summarize compact v2 interrupts and retain legacy visibility.

    This is display normalization, not authorization.  The MCP adapter must
    reject ambiguous or malformed human-owned interrupts during resume.
    """
    if not isinstance(value, dict):
        return {
            "actor": "agent",
            "interaction": "task",
            "action": "respond",
            "requires_approval": False,
        }

    actor = value.get("actor")
    interaction = value.get("interaction")
    if actor in {"agent", "human"} and isinstance(interaction, str):
        if interaction not in _ACTIONS:
            raise ValidationError(f"Unknown interrupt interaction: {interaction!r}")
        if (interaction == "task") != (actor == "agent"):
            raise ValidationError("Interrupt actor and interaction are inconsistent")
    elif actor in {"agent", "human"} and interaction is None:
        interaction = "task" if actor == "agent" else "review"
    else:
        nested = value.get("interaction")
        mode = (
            nested.get("mode")
            if isinstance(nested, dict)
            else value.get("interaction_mode")
        )
        interaction = nested.get("type") if isinstance(nested, dict) else nested
        if interaction not in _ACTIONS:
            interaction = (
                "approval" if mode == "approval" or "approval" in value else "task"
            )
        actor = "human" if interaction in {"review", "approval"} else "agent"

    return {
        "actor": actor,
        "interaction": interaction,
        "action": value.get("action") or value.get("step") or "respond",
        "requires_approval": actor == "human" and interaction == "approval",
    }
