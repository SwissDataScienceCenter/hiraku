"""Stdio MCP server exposing Hiraku records, runs, and workflows.

The server is the MCP SDK's `MCPServer`. Its tools and prompts come from
providers that return SDK objects: `Workflow.tools` and `Workflow.prompts`,
`Session.tools`, and `Record.tools`. They are fixed when the server starts,
because coding agents do not reliably pick up changes to the tool list.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .agent import load_context
from .config import HirakuConfig
from .errors import GitError, HirakuError
from .git import GitRepository
from .record import Record, load_record
from .session import Session
from .workflow import Workflow, load_workflows

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, and runs workflows that guide you step by step.
Start a workflow with the tool named after it (workflow_list lists them), and
answer its instructions with workflow_reply. Use session_list and session_show
to resume earlier sessions. Launch experiments with the `hiraku launch` CLI.\
"""


def create_server(config: HirakuConfig) -> MCPServer:
    """Create the Hiraku MCP server for one project.

    Workflows are loaded once; a workflow added or changed later needs a new
    server, e.g. by reconnecting it in the coding agent.
    """
    path = config.path

    def project() -> HirakuConfig:
        # Re-read on every call, so changes made elsewhere, such as a new active
        # record, are visible to a long-running server.
        return HirakuConfig.load(path)

    workflows = load_workflows(config)
    server = MCPServer("hiraku", version=_version(), instructions=INSTRUCTIONS)
    tools = [
        _project_status(project),
        *Workflow.tools(project, workflows),
        *Session.tools(project, workflows),
        *Record.tools(project),
    ]
    for tool in tools:
        server.add_tool(
            _json_result(tool.fn),
            name=tool.name,
            title=tool.title,
            description=tool.description,
            annotations=tool.annotations,
            structured_output=False,
        )
    for prompt in Workflow.prompts(project, workflows):
        server.add_prompt(prompt)
    _register_resources(server, project, workflows)
    return server


def serve(config: HirakuConfig) -> None:
    """Run the Hiraku MCP server over stdio for one project."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="hiraku: %(message)s"
    )
    anyio.run(create_server(config).run_stdio_async)


def _project_status(project: Callable[[], HirakuConfig]) -> Tool:
    def project_status() -> dict[str, Any]:
        config = project()
        try:
            branch = GitRepository(config).current_branch
        except GitError:
            branch = None
        return to_jsonable_python(
            {
                "project_root": config.root,
                "active_record_id": config.active_record_id,
                "tracking_backend": config.tracking_backend,
                "git_branch": branch,
            }
        )

    return Tool.from_function(
        project_status,
        description="Show the project root, active record, tracking backend, and "
        "Git branch.",
        annotations=ToolAnnotations(read_only_hint=True),
    )


def _register_resources(
    server: MCPServer,
    project: Callable[[], HirakuConfig],
    workflows: dict[str, Workflow],
) -> None:
    @server.resource(
        "hiraku://records/{record_id}",
        name="record",
        description="Full Markdown file of a research record",
        mime_type="text/markdown",
    )
    def record(record_id: str) -> str:
        return _read(_resource(load_record, record_id, config=project()).path)

    @server.resource(
        "hiraku://sessions/{session_id}",
        name="session",
        description="Full stored state of a workflow session",
        mime_type="application/json",
    )
    def session(session_id: str) -> str:
        stored = _resource(Session.load, session_id, config=project())
        return _dumps(stored.show(workflows.get(stored.workflow)))

    @server.resource(
        "hiraku://workflows/{workflow_id}",
        name="workflow",
        description="Prompt document of a workflow",
        mime_type="text/markdown",
    )
    def workflow(workflow_id: str) -> str:
        if workflow_id not in workflows:
            raise ResourceError(f"Unknown workflow {workflow_id!r}")
        return workflows[workflow_id].text

    @server.resource(
        "hiraku://contexts/{context_id}",
        name="context",
        description="Curated project context",
        mime_type="text/markdown",
    )
    def context(context_id: str) -> str:
        return _read(_resource(load_context, context_id, config=project()).path)


def _json_result(function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function to return JSON text and Hiraku errors as tool errors.

    The wrapper keeps the function's signature, which defines the tool schema.
    """

    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            return _text(_dumps(function(*args, **kwargs)))
        except (HirakuError, PydanticValidationError) as exc:
            return _text(str(exc), error=True)

    return run


def _resource(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except HirakuError as exc:
        raise ResourceError(str(exc)) from exc


def _read(path: Path | None) -> str:
    assert path is not None
    return path.read_text(encoding="utf-8")


def _text(text: str, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _version() -> str:
    try:
        return version("hiraku")
    except PackageNotFoundError:  # pragma: no cover - source checkouts
        return "0"
