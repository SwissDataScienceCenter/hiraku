"""Optional local MCP interface for Hiraku."""

from .server import create_server
from .tools import WorkflowApproval

__all__ = ["WorkflowApproval", "create_server"]
