"""Read-only MCP resources backed by Hiraku project state."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..core.agent import (
    list_contexts,
    list_programs,
    load_context,
    load_program,
)
from ..core.config import HirakuConfig
from ..core.record import Record, list_records, load_record


def register(
    server: MCPServer, get_config: Callable[[], HirakuConfig]
) -> dict[str, Any]:
    """Register project, record, Program, and Context resources."""

    @server.resource("hiraku://project", name="project")
    def project_resource() -> dict[str, Any]:
        """Expose project configuration and available Hiraku resource IDs."""
        config = get_config()
        return {
            "config": config.to_dict(),
            "records": [record.id for record in list_records(config=config)],
            "programs": [program.id for program in list_programs(config=config)],
            "contexts": [context.id for context in list_contexts(config=config)],
        }

    @server.resource(
        "hiraku://records/{record_id}", name="records", mime_type="application/json"
    )
    def record_resource(record_id: str) -> str:
        """Expose a record with its inherited effective research context."""
        config = get_config()
        record = load_record(record_id, config=config)
        return json.dumps(_record_document(record, config), default=str)

    @server.resource("hiraku://programs/{program_id}", name="programs")
    def program_resource(program_id: str) -> str:
        """Expose a project's research Program as Markdown."""
        program = load_program(program_id, config=get_config())
        return program.path.read_text(encoding="utf-8")

    @server.resource("hiraku://contexts/{context_id}", name="contexts")
    def context_resource(context_id: str) -> str:
        """Expose a project's curated Context as Markdown."""
        context = load_context(context_id, config=get_config())
        return context.path.read_text(encoding="utf-8")

    return {
        "project_resource": project_resource,
        "record_resource": record_resource,
        "program_resource": program_resource,
        "context_resource": context_resource,
        "record_document": _record_document,
    }


def _record_document(record: Record, config: HirakuConfig) -> dict[str, Any]:
    """Build the stored and inherited view shared by resources and prompts."""
    resolved = record.resolve(config=config)
    document = {
        "record": record.to_dict(),
        "effective": resolved.values,
        "sources": resolved.sources,
        "markdown": record.markdown,
    }
    if record.type == "task":
        document["linked_contexts"] = [
            context.id
            for context in list_contexts(config=config)
            if getattr(context, "task_id", None) == record.id
        ]
    return document
