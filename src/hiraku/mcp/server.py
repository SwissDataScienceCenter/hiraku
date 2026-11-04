"""MCP server definition for a Hiraku project."""

from __future__ import annotations

import mimetypes
from typing import Any
from urllib.parse import unquote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import (
    ResourceError,
    ToolError,
    UnexpectedResourceError,
    UnexpectedToolError,
)
from pydantic import ValidationError as PydanticValidationError

from ..errors import HirakuError
from ..project import Project
from .tools import register_tools

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, runs commands with execute, and runs LangGraph
workflows. Use workflow_start, workflow_status, and workflow_resume to operate
a workflow. When an interrupt requests approval, workflow_resume opens a
structured MCP elicitation and waits for the user's answer before resuming.
Use workflow_sessions and workflow_session_history to inspect recorded
sessions and their history.
"""


class ProjectMCP(MCPServer):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except UnexpectedToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, HirakuError | PydanticValidationError):
                raise ToolError(str(cause)) from cause
            raise

    async def read_resource(self, uri: Any, context: Any = None) -> Any:
        try:
            contents = await super().read_resource(uri, context)
        except UnexpectedResourceError as exc:
            if isinstance(exc.__cause__, HirakuError):
                raise ResourceError(str(exc.__cause__)) from exc.__cause__
            raise
        if str(uri).startswith("hiraku://sources/"):
            mime = mimetypes.guess_type(unquote(str(uri)))[0]
            for item in contents:
                item.mime_type = mime or "application/octet-stream"
        return contents


def create_server(config: Project | None = None) -> ProjectMCP:
    """Create a stdio-capable server with optional project binding.

    When config is omitted, project discovery is deferred until a project-bound
    tool or resource is called.
    """
    server = ProjectMCP("Hiraku", instructions=INSTRUCTIONS)
    register_tools(server, config)
    return server
