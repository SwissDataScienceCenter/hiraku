"""MCP server composition for Hiraku's tools, prompts, and resources."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..core.config import HirakuConfig
from .prompts import register as register_prompts
from .resources import register as register_resources
from .schemas import (
    Analysis,
    ExperimentProposal,
    LiteratureReview,
    LiteratureSource,
    ProposedEntrypoint,
    ResearchTask,
)
from .tools.experiments import register as register_experiment_tools
from .tools.literature import register as register_literature_tools
from .tools.records import register as register_record_tools

_active_config: ContextVar[HirakuConfig | None] = ContextVar(
    "hiraku_mcp_config", default=None
)

mcp = MCPServer(
    name="hiraku",
    title="Hiraku MCP Server",
    description="Hiraku research state and operations",
    instructions=(
        "Hiraku research state and validated operations. "
        "launch_experiment returns a launch ID immediately; poll "
        "get_launch_status until the process is terminal before analyzing runs "
        "or starting a follow-up experiment."
    ),
)


def _config() -> HirakuConfig:
    """Return the project config bound to this MCP server session."""
    return _active_config.get() or HirakuConfig.discover()


_tool_handlers = register_record_tools(mcp, _config)
_tool_handlers.update(register_experiment_tools(mcp, _config))
_tool_handlers.update(register_literature_tools(mcp, _config))
create_task: Any = _tool_handlers["create_task"]
list_runs: Any = _tool_handlers["list_runs"]
propose_experiment: Any = _tool_handlers["propose_experiment"]
commit_implementation: Any = _tool_handlers["commit_implementation"]
launch_experiment: Any = _tool_handlers["launch_experiment"]
get_launch_status: Any = _tool_handlers["get_launch_status"]
record_analysis: Any = _tool_handlers["record_analysis"]
save_literature_review: Any = _tool_handlers["save_literature_review"]

_resource_handlers = register_resources(mcp, _config)
project_resource: Any = _resource_handlers["project_resource"]
record_resource: Any = _resource_handlers["record_resource"]
program_resource: Any = _resource_handlers["program_resource"]
context_resource: Any = _resource_handlers["context_resource"]

_prompt_handlers = register_prompts(mcp, _config, _resource_handlers["record_document"])
program_prompt_messages: Any = _prompt_handlers["program_prompt_messages"]
propose_experiment_prompt: Any = _prompt_handlers["propose_experiment_prompt"]
register_program_prompts: Any = _prompt_handlers["register_program_prompts"]


def create_server(config: HirakuConfig | None = None) -> MCPServer:
    """Return Hiraku's module-level MCP API server."""
    selected = config or HirakuConfig.discover()
    _active_config.set(selected)
    register_program_prompts(selected)
    return mcp


def serve(config: HirakuConfig | None = None) -> None:
    """Run Hiraku's local stdio MCP server."""
    token = _active_config.set(config)
    try:
        register_program_prompts(config or HirakuConfig.discover())
        mcp.run(transport="stdio")
    finally:
        _active_config.reset(token)


__all__ = [
    "Analysis",
    "ExperimentProposal",
    "LiteratureReview",
    "LiteratureSource",
    "ProposedEntrypoint",
    "ResearchTask",
    "commit_implementation",
    "context_resource",
    "create_server",
    "create_task",
    "get_launch_status",
    "launch_experiment",
    "list_runs",
    "mcp",
    "program_prompt_messages",
    "program_resource",
    "project_resource",
    "propose_experiment",
    "propose_experiment_prompt",
    "record_analysis",
    "record_resource",
    "save_literature_review",
    "serve",
]
