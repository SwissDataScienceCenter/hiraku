"""MCP tools, resources, and prompts for a Hiraku project."""

from __future__ import annotations

import json
from typing import Annotated, Any
from urllib.parse import quote, unquote

from anyio.from_thread import check_cancelled
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic_core import to_jsonable_python

from ..errors import ValidationError
from ..execution import execute_command as execute_project_command
from ..project import Project
from ..record import Record, list_records, load_record


def _data(record: Record) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude={"path"})


def _update(record: Record, fields: dict[str, Any], markdown: str | None) -> Record:
    protected = {"id", "path", "markdown", "parent_id"}
    invalid = protected.intersection(fields)
    if invalid:
        raise ValidationError(f"Cannot update fields: {sorted(invalid)}")
    values = {
        **record.model_dump(exclude_unset=True, exclude={"path"}),
        **fields,
    }
    if markdown is not None:
        values["markdown"] = markdown
    updated = type(record).model_validate(values)
    updated.save(record.path, overwrite=True)
    return updated


def _get_project(config: Project | None) -> Project:
    return config or Project.discover()


def register_project_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register project-level tools.

    Tools:
    - `project_info`: Read the current project configuration and metadata.
    """

    @server.tool(
        title="Project information",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def project_info() -> dict[str, Any]:
        """Read the current project configuration and metadata."""
        return to_jsonable_python(_get_project(config).to_dict(), fallback=dict)


def register_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register all Hiraku MCP capabilities on a server.

    Features include project- and record-level tools, prompts, and resources.
    """
    register_project_tools(server, config)
    register_record_tools(server, config)
    register_record_resources(server, config)
    register_record_prompts(server, config)


def register_record_tools(server: MCPServer, config: Project | None) -> None:
    """
    Register record, execution, and tracked-run tools.

    Tools:
    - `record_list`: List research records in the selected project.
    - `record_show`: Read a research record and its effective inherited fields.
    - `record_create`: Create a research record, optionally activating it.
    - `record_update`: Merge record fields and optionally replace Markdown.
    - `record_reparent`: Move a record subtree under a parent, or make it a root.
    - `record_delete`: Delete a record using Hiraku's relationship checks.
    - `record_runs`: List tracked runs attributed to a record.
    - `execute_command`: Run a command and return output with exit code.
    """

    @server.tool(
        title="List research records",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_list() -> list[dict[str, Any]]:
        """List research records in the selected project."""
        project = _get_project(config)
        return [_data(item) for item in list_records(project=project)]

    @server.tool(
        title="Show research record",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_show(record_id: str) -> dict[str, Any]:
        """Read a research record and its effective inherited fields."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        resolved = record.resolve(project=project)
        return {
            **_data(record),
            "resolved": resolved.values,
            "sources": resolved.sources,
        }

    @server.tool(
        title="Create research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def record_create(
        record_id: str,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        fields: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Create a research record, optionally activating it like the CLI."""
        project = _get_project(config)
        extras = fields or {}
        reserved = {
            "id",
            "path",
            "parent_id",
            "related_to",
            "markdown",
            "record_id",
            "activate",
            "project",
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
                project=project,
                **extras,
            )
        )

    @server.tool(
        title="Update research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def record_update(
        record_id: str,
        fields: dict[str, Any],
        markdown: str | None = None,
    ) -> dict[str, Any]:
        """Merge record fields and optionally replace Markdown; null clears a field."""
        project = _get_project(config)
        return _data(_update(load_record(record_id, project=project), fields, markdown))

    @server.tool(
        title="Reparent research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def record_reparent(record_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Move a record subtree under a parent, or make it a root."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        record.reparent(parent_id, project=project)
        return _data(record)

    @server.tool(
        title="Delete research record",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def record_delete(record_id: str) -> dict[str, str]:
        """Delete a record using Hiraku's relationship checks."""
        project = _get_project(config)
        load_record(record_id, project=project).delete(project=project)
        return {"deleted": record_id}

    @server.tool(
        title="Execute project command",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def execute_command(
        record_id: str,
        command: list[str],
        dispatch: str | None = None,
    ) -> dict[str, Any]:
        """Run a command and return output with exit code."""
        project = _get_project(config)
        result = execute_project_command(
            command,
            record_id=record_id,
            dispatch=dispatch,
            capture_output=True,
            project=project,
            cancel_check=check_cancelled,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }

    @server.tool(
        title="List tracked runs",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def record_runs(
        record_id: str,
        all_records: bool = False,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List tracked runs attributed to a record, like `hiraku runs`."""
        if ascending is not None and sort is None:
            raise ValidationError("A sort direction requires sort")
        project = _get_project(config)
        record = load_record(record_id, project=project)
        query: dict[str, Any] = {"limit": limit} if limit is not None else {}
        if sort is not None:
            declarations = record.resolve(project=project).values.get("metrics") or []
            directions = {item.name: item.direction for item in declarations}
            query["sort"] = sort
            query["ascending"] = (
                ascending
                if ascending is not None
                else directions.get(sort) != "maximize"
            )
        runs = record.resolve_tracking_backend(project=project).list_runs(
            record_id=None if all_records else record.id,
            **query,
        )
        return to_jsonable_python(runs, fallback=dict)


def register_record_resources(
    server: MCPServer,
    config: Project | None,
) -> None:
    """Register actual attached files, excluding Markdown records."""

    @server.resource(
        "hiraku://catalog",
        mime_type="application/json",
    )
    def catalog() -> str:
        project = _get_project(config)
        records = list_records(project=project)

        # Identify all record Markdown files.
        record_paths = {record.path.resolve() for record in records}

        uris: list[str] = []

        for record in records:
            root = record.path.parent.resolve()

            for path in sorted(root.rglob("*")):
                resolved = path.resolve()

                if not resolved.is_file():
                    continue

                if not resolved.is_relative_to(root):
                    continue

                # Do not expose tasks or experiments as attachments.
                if resolved in record_paths:
                    continue

                relative = path.relative_to(root).as_posix()

                uris.append(f"hiraku://sources/{record.id}/{quote(relative, safe='')}")

        return json.dumps(sorted(set(uris)))

    @server.resource("hiraku://sources/{record_id}/{source_path}")
    def source_resource(
        record_id: str,
        source_path: str,
    ) -> bytes:
        project = _get_project(config)
        record = load_record(record_id, project=project)

        root = record.path.parent.resolve()
        path = (root / unquote(source_path)).resolve()

        if not path.is_relative_to(root) or not path.is_file():
            raise ResourceError("Source must be inside the record directory")

        # Also enforce the exclusion when reading directly.
        record_paths = {item.path.resolve() for item in list_records(project=project)}

        if path in record_paths:
            raise ResourceError("Record Markdown files are not attached sources")

        return path.read_bytes()


def register_record_prompts(server: MCPServer, config: Project | None) -> None:
    """Register prompts grounded in records and related evidence."""

    @server.prompt(title="Propose implementation")
    def propose_implementation(
        record_id: Annotated[
            str, Field(description="Research record ID to ground the proposal in.")
        ],
        related_record_ids: Annotated[
            str,
            Field(description="Optional comma-separated related record IDs."),
        ] = "",
    ) -> str:
        """Ground a proposal in a record and comma-separated related records."""
        project = _get_project(config)
        record = load_record(record_id, project=project)
        related = [
            load_record(identifier.strip(), project=project)
            for identifier in related_record_ids.split(",")
            if identifier.strip()
        ]
        evidence = "\n\n".join(f"## {item.id}\n{item.markdown}" for item in related)
        return (
            "Propose the next implementation for this research record. Cite the "
            "curated evidence, distinguish observations from assumptions, and "
            "describe validation before making changes. Inspect attached sources "
            "through hiraku://catalog when needed.\n\n"
            f"Record: {json.dumps(_data(record))}\n\nRelated records:\n{evidence}"
        )
