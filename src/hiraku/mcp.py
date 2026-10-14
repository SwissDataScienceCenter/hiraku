"""Optional local MCP interface to a single Hiraku project."""

from __future__ import annotations

import json
import mimetypes
from typing import Any
from urllib.parse import quote, unquote

from pydantic import ValidationError as PydanticValidationError
from pydantic_core import to_jsonable_python

from .agent import (
    Context,
    list_actions,
    list_contexts,
    list_programs,
    load_action,
    load_context,
    load_program,
)
from .base import BaseRecord
from .config import HirakuConfig
from .errors import HirakuError, ValidationError
from .launch import launch, render_arguments
from .record import Record, list_records, load_record


def _data(record: BaseRecord) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def _update(
    record: BaseRecord, fields: dict[str, Any], markdown: str | None
) -> BaseRecord:
    protected = {"id", "path", "markdown", "parent_id"}
    if protected.intersection(fields):
        raise ValidationError(f"Cannot update fields: {sorted(protected)}")
    values = {
        **record.model_dump(exclude_unset=True, exclude={"path"}),
        **fields,
    }
    if markdown is not None:
        values["markdown"] = markdown
    updated = type(record).model_validate(values)
    updated.save(record.path, overwrite=True)
    return updated


def create_server(config: HirakuConfig | None = None) -> Any:
    """Create a stdio-capable server bound to a discovered or supplied project.

    The MCP SDK is a regular Hiraku dependency. Resources are read afresh on
    each request so external edits and tool mutations remain visible.
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import (
        ResourceError,
        ToolError,
        UnexpectedToolError,
    )

    selected = config or HirakuConfig.discover()

    class ProjectMCP(MCPServer):
        async def call_tool(
            self, name: str, arguments: dict[str, Any], context: Any = None
        ) -> Any:
            try:
                return await super().call_tool(name, arguments, context)
            except UnexpectedToolError as exc:
                # The SDK hides unexpected messages; Hiraku's validation errors
                # tell the agent how to correct its call.
                cause = exc.__cause__
                if isinstance(cause, HirakuError | PydanticValidationError):
                    raise ToolError(str(cause)) from cause
                raise

        async def read_resource(self, uri: Any, context: Any = None) -> Any:
            contents = await super().read_resource(uri, context)
            # Source templates contain many file types; MIME depends on the URI.
            if str(uri).startswith("hiraku://sources/"):
                mime = mimetypes.guess_type(unquote(str(uri)))[0]
                for item in contents:
                    item.mime_type = mime or "application/octet-stream"
            return contents

    server = ProjectMCP("Hiraku")

    @server.tool()
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        return [_data(item) for item in list_records(config=selected)]

    @server.tool()
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record and its effective inherited fields."""
        record = load_record(record_id, config=selected)
        resolved = record.resolve(config=selected)
        return {
            **_data(record),
            "resolved": resolved.values,
            "sources": resolved.sources,
        }

    @server.tool()
    def record_create(
        record_id: str,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        extras = fields or {}
        reserved = {
            "id",
            "path",
            "parent_id",
            "related_to",
            "markdown",
            "record_id",
            "activate",
            "config",
        }
        if reserved.intersection(extras):
            raise ValidationError("Use named arguments for identity and relationships")
        return _data(
            Record.create(
                record_id,
                parent_id=parent_id,
                related_to=related_to,
                markdown=markdown,
                activate=activate,
                config=selected,
                **extras,
            )
        )

    @server.tool()
    def record_update(
        record_id: str,
        fields: dict[str, Any],
        markdown: str | None = None,
    ) -> dict[str, Any]:
        """Merge record fields and optionally replace Markdown; null clears a field."""
        return _data(_update(load_record(record_id, config=selected), fields, markdown))

    @server.tool()
    def record_reparent(record_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Move a record subtree under a parent, or make it a root."""
        record = load_record(record_id, config=selected)
        record.reparent(parent_id, config=selected)
        return _data(record)

    @server.tool()
    def record_delete(record_id: str) -> dict[str, str]:
        """Delete a record using Hiraku's relationship checks."""
        load_record(record_id, config=selected).delete(config=selected)
        return {"deleted": record_id}

    @server.tool()
    def context_list() -> list[dict[str, Any]]:
        """List curated contexts."""
        return [_data(item) for item in list_contexts(config=selected)]

    @server.tool()
    def context_show(context_id: str) -> dict[str, Any]:
        """Read a curated context."""
        return _data(load_context(context_id, config=selected))

    @server.tool()
    def context_create(
        context_id: str, markdown: str = "", fields: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a context with Markdown and optional metadata."""
        extras = fields or {}
        if {"id", "path", "markdown"}.intersection(extras):
            raise ValidationError("Use named arguments for identity and Markdown")
        context = Context(id=context_id, markdown=markdown, **extras)
        context.save(selected.contexts_dir / context.id / "context.md")
        return _data(context)

    @server.tool()
    def context_update(
        context_id: str, fields: dict[str, Any], markdown: str | None = None
    ) -> dict[str, Any]:
        """Merge context metadata and optionally replace its Markdown."""
        return _data(
            _update(load_context(context_id, config=selected), fields, markdown)
        )

    @server.tool()
    def context_delete(context_id: str) -> dict[str, str]:
        """Delete context metadata; attached source files remain on disk."""
        load_context(context_id, config=selected).delete()
        return {"deleted": context_id}

    @server.tool()
    def launch_entrypoint(
        record_id: str,
        entrypoint_name: str,
        args: dict[str, Any] | None = None,
        commit: str | None = None,
        dispatch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Launch a configured entrypoint with attribution, or preview its command."""
        entrypoint = load_record(record_id, config=selected).entrypoint(
            entrypoint_name, config=selected
        )
        if dry_run:
            return {
                "command": [
                    *entrypoint.command,
                    *render_arguments({**entrypoint.args, **(args or {})}),
                ]
            }
        result = launch(
            entrypoint,
            name=entrypoint_name,
            args=args,
            record_id=record_id,
            commit=commit,
            dispatch=dispatch,
            capture_output=True,
            check=False,
            config=selected,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @server.tool()
    def runs(
        record_id: str,
        all_records: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List tracked runs attributed to a record, like `hiraku runs`.

        With all_records, list every attributed run in the record's tracking
        source. Sorting by a declared metric follows its direction unless
        ascending is given.
        """
        if ascending is not None and sort is None:
            raise ValidationError("A sort direction requires sort")
        record = load_record(record_id, config=selected)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = record.resolve(config=selected).values.get("metrics") or []
            directions = {item.name: item.direction for item in declarations}
            query["sort"] = sort
            query["ascending"] = (
                ascending
                if ascending is not None
                else directions.get(sort) != "maximize"
            )
        found = record.resolve_tracking_backend(config=selected).list_runs(
            record_id=None if all_records else record.id, **query
        )
        # Runs keep their fields in read-only mappings.
        return to_jsonable_python(found, fallback=dict)

    @server.resource("hiraku://catalog", mime_type="application/json")
    def catalog() -> str:
        """Discover current records, contexts, programs, actions and source files."""
        uris = [f"hiraku://records/{item.id}" for item in list_records(config=selected)]
        uris += [
            f"hiraku://programs/{item.id}" for item in list_programs(config=selected)
        ]
        uris += [
            f"hiraku://actions/{item.entity}/{item.id}"
            for item in list_actions(config=selected)
        ]
        for context in list_contexts(config=selected):
            uris.append(f"hiraku://contexts/{context.id}")
            root = (selected.contexts_dir / context.id).resolve()
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.resolve().is_relative_to(root):
                    relative = path.relative_to(root).as_posix()
                    uris.append(
                        f"hiraku://sources/{context.id}/{quote(relative, safe='')}"
                    )
        return json.dumps(uris)

    @server.resource("hiraku://records/{record_id}", mime_type="application/json")
    def record_resource(record_id: str) -> str:
        return json.dumps(_data(load_record(record_id, config=selected)))

    @server.resource("hiraku://contexts/{context_id}")
    def context_resource(context_id: str) -> str:
        return load_context(context_id, config=selected).markdown

    @server.resource("hiraku://programs/{program_id}")
    def program_resource(program_id: str) -> str:
        return load_program(program_id, config=selected).markdown

    @server.resource("hiraku://actions/{entity}/{action_id}")
    def action_resource(entity: str, action_id: str) -> str:
        return load_action(action_id, entity=entity, config=selected).markdown

    @server.resource("hiraku://sources/{context_id}/{source_path}")
    def source_resource(context_id: str, source_path: str) -> bytes:
        load_context(context_id, config=selected)
        root = (selected.contexts_dir / context_id).resolve()
        path = (root / unquote(source_path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be a file inside its context directory")
        return path.read_bytes()

    @server.prompt()
    def propose_implementation(record_id: str, context_ids: str = "") -> str:
        """Ground a proposal in a record and comma-separated contexts."""
        record = load_record(record_id, config=selected)
        contexts = [
            load_context(identifier.strip(), config=selected)
            for identifier in context_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in contexts)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Inspect attached sources "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(_data(record))}\n\nContexts:\n{evidence}"
        )

    return server
