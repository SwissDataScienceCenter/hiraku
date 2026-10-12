"""Stdio MCP server exposing Hiraku records, runs, and workflows."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import unquote

import anyio
import anyio.to_thread
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    PromptsListChanged,
    ResourcesListChanged,
    ToolsListChanged,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from .agent import list_contexts, load_context
from .config import HirakuConfig
from .errors import (
    GitError,
    HirakuError,
    NoActiveRecordError,
    ValidationError,
    WorkflowError,
)
from .git import GitRepository
from .io import json_value
from .record import Record, list_records, load_record, update_record
from .workflow.service import WorkflowService

logger = logging.getLogger(__name__)

try:  # The modern protocol era is defined by the MCP types package.
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS
except ImportError:  # pragma: no cover - depends on the installed SDK
    MODERN_PROTOCOL_VERSIONS = ()

MARKDOWN_LIMIT = 8000
DEFAULT_RUN_LIMIT = 20

INSTRUCTIONS = """\
Hiraku documents research in records (goals, hypotheses, experiments, findings),
attributes tracked runs to them, and runs workflows that guide you step by step.
Use workflow_list to see the workflows and their arguments, and the
<workflow>_start tool to begin one. Every workflow response names the reply tools
that continue it. Use session_list and session_show to resume earlier sessions.
Launch experiments with the `hiraku launch` CLI.\
"""


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _WorkflowArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str = Field(description="Workflow ID from workflow_list.")


class _SessionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(description="Session ID from session_list.")


class _RecordShowArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str | None = Field(
        default=None, description="Record to show; defaults to the active record."
    )
    raw: bool = Field(
        default=False, description="Show stored fields only, without inheritance."
    )


class _RecordCreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str = Field(description="New record ID.")
    parent_id: str | None = Field(default=None, description="Parent record ID.")
    related_to: list[str] = Field(
        default_factory=list, description="Related record IDs."
    )
    type: str | None = Field(
        default=None, description="Record role, e.g. task, experiment, dataset."
    )
    commit: str | None = Field(
        default=None, description="Git ref to bind; resolved to a full hash."
    )
    markdown: str = Field(default="", description="Markdown body.")
    fields: dict[str, Any] = Field(
        default_factory=dict, description="Additional stored fields."
    )
    activate: bool = Field(
        default=False, description="Make the new record the active record."
    )


class _RecordUpdateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str = Field(description="Record to update.")
    fields: dict[str, Any] = Field(
        default_factory=dict, description="Stored fields to set."
    )
    unset: list[str] = Field(
        default_factory=list, description="Stored fields to remove."
    )
    parent_id: str | None = Field(default=None, description="New parent record ID.")
    make_root: bool = Field(default=False, description="Make the record a root.")
    related_to: list[str] | None = Field(
        default=None, description="Replacement list of related record IDs."
    )
    commit: str | None = Field(
        default=None, description="Git ref to bind; resolved to a full hash."
    )
    markdown: str | None = Field(default=None, description="Replacement Markdown body.")


class _RunsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str | None = Field(
        default=None, description="Record whose runs to list; defaults to active."
    )
    all: bool = Field(
        default=False,
        description="List every attributed run in the record's tracking source.",
    )
    sort: str | None = Field(default=None, description="Field or metric to sort by.")
    ascending: bool | None = Field(
        default=None, description="Sort direction; defaults to the metric direction."
    )
    limit: int = Field(
        default=DEFAULT_RUN_LIMIT, ge=1, le=200, description="Maximum runs returned."
    )


@dataclass(frozen=True)
class _BuiltinTool:
    description: str
    arguments: type[BaseModel]
    function: Callable[[Any], Any]
    read_only: bool = True


class HirakuMCPServer:
    """The Hiraku MCP server for one project.

    Args:
        config: Project bound at startup.
        exposed: Workflow IDs to expose instead of the configured set.
    """

    def __init__(self, config: HirakuConfig, *, exposed: list[str] | None = None):
        self.service = WorkflowService(config, exposed=exposed)
        self.bus = InMemorySubscriptionBus()
        self.builtins: dict[str, _BuiltinTool] = {
            "workflow_list": _BuiltinTool(
                "List all project workflows, whether they are active, and their "
                "start arguments.",
                _NoArguments,
                lambda _: self.service.list_workflows(),
            ),
            "workflow_activate": _BuiltinTool(
                "Expose a workflow's tools and save the workflow set in hiraku.yml.",
                _WorkflowArguments,
                lambda args: self.service.activate(args.workflow_id),
                read_only=False,
            ),
            "workflow_deactivate": _BuiltinTool(
                "Hide a workflow's tools and save the workflow set in hiraku.yml. "
                "Sessions are not affected.",
                _WorkflowArguments,
                lambda args: self.service.deactivate(args.workflow_id),
                read_only=False,
            ),
            "session_list": _BuiltinTool(
                "List workflow sessions with their status, newest first.",
                _NoArguments,
                lambda _: self.service.list_sessions(),
            ),
            "session_show": _BuiltinTool(
                "Show a workflow session: orientation, pending instruction or "
                "result, and stored data. Use it to resume a session.",
                _SessionArguments,
                lambda args: self.service.show(args.session_id),
            ),
            "session_cancel": _BuiltinTool(
                "Cancel a workflow session. Records, code, and runs are kept.",
                _SessionArguments,
                lambda args: self.service.cancel(args.session_id),
                read_only=False,
            ),
            "project_status": _BuiltinTool(
                "Show the project root, active record, tracking backend, and Git "
                "branch.",
                _NoArguments,
                lambda _: self._project_status(),
            ),
            "record_list": _BuiltinTool(
                "List research records with their parent, type, status, and decision.",
                _NoArguments,
                lambda _: self._record_list(),
            ),
            "record_show": _BuiltinTool(
                "Show a record's effective fields with the records defining them, "
                "including resolved entrypoints.",
                _RecordShowArguments,
                self._record_show,
            ),
            "record_create": _BuiltinTool(
                "Create a root or child research record.",
                _RecordCreateArguments,
                self._record_create,
                read_only=False,
            ),
            "record_update": _BuiltinTool(
                "Update a record's stored fields, parent, links, commit, or body.",
                _RecordUpdateArguments,
                self._record_update,
                read_only=False,
            ),
            "runs": _BuiltinTool(
                "List tracked runs attributed to a record.",
                _RunsArguments,
                self._runs,
            ),
        }
        self.server = Server(
            "hiraku",
            version=_version(),
            instructions=INSTRUCTIONS,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_list_resource_templates=self._list_resource_templates,
            on_read_resource=self._read_resource,
            on_list_prompts=self._list_prompts,
            on_get_prompt=self._get_prompt,
            on_subscriptions_listen=ListenHandler(self.bus),
        )

    @property
    def config(self) -> HirakuConfig:
        """Return the project configuration as currently saved on disk."""
        return self.service.current_config()

    def initialization_options(self) -> Any:
        """Return initialization options advertising list-change notifications."""
        return self.server.create_initialization_options(
            NotificationOptions(tools_changed=True, prompts_changed=True)
        )

    async def run_stdio(self) -> None:
        """Serve the protocol over stdin and stdout until the client disconnects."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream, self.initialization_options()
            )

    # Tools --------------------------------------------------------------------

    async def _list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name=name,
                description=tool.description,
                input_schema=_schema(tool.arguments),
                annotations=types.ToolAnnotations(read_only_hint=tool.read_only),
            )
            for name, tool in self.builtins.items()
        ]
        tools.extend(
            types.Tool(
                name=definition["name"],
                description=definition["description"],
                input_schema=definition["input_schema"],
            )
            for definition in self.service.tool_definitions()
        )
        return types.ListToolsResult(tools=tools)

    async def _call_tool(self, ctx: Any, params: Any) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        try:
            result = await anyio.to_thread.run_sync(
                partial(self.call_tool, name, arguments)
            )
        except (HirakuError, PydanticValidationError) as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                is_error=True,
            )
        if (
            name in {"workflow_activate", "workflow_deactivate"}
            and isinstance(result, dict)
            and result.get("changed")
        ):
            await self._notify_lists_changed(ctx)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_dumps(result))]
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a built-in or exposed workflow tool synchronously.

        Raises:
            hiraku.errors.HirakuError: If the tool is unknown or fails.
        """
        builtin = self.builtins.get(name)
        if builtin is not None:
            return builtin.function(builtin.arguments.model_validate(arguments))
        if self.service.is_exposed_tool(name):
            return self.service.call(name, arguments)
        try:
            entry, _ = self.service.catalog.tool(name)
        except WorkflowError as exc:
            raise WorkflowError(f"Unknown tool {name!r}") from exc
        raise WorkflowError(
            f"Workflow {entry.id!r} is not active; call workflow_activate first"
        )

    async def _notify_lists_changed(self, ctx: Any) -> None:
        try:
            if getattr(ctx, "protocol_version", None) in MODERN_PROTOCOL_VERSIONS:
                await self.bus.publish(ToolsListChanged())
                await self.bus.publish(PromptsListChanged())
                await self.bus.publish(ResourcesListChanged())
            else:
                await ctx.session.send_tool_list_changed()
                await ctx.session.send_prompt_list_changed()
                await ctx.session.send_resource_list_changed()
        except Exception:
            logger.warning("Could not send list-changed notifications", exc_info=True)

    # Built-in tool implementations --------------------------------------------

    def _project_status(self) -> dict[str, Any]:
        config = self.config
        try:
            branch = GitRepository(config).current_branch
        except GitError:
            branch = None
        return json_value(
            {
                "project_root": config.root,
                "active_record_id": config.active_record_id,
                "tracking_backend": config.tracking_backend,
                "git_branch": branch,
                "active_workflows": self.service.exposed_ids(),
            }
        )

    def _record_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "parent_id": record.parent_id,
                "type": record.type,
                "status": record.status,
                "decision": record.decision,
            }
            for record in list_records(config=self.config)
        ]

    def _active(self, record_id: str | None) -> Record:
        identifier = record_id or self.config.active_record_id
        if identifier is None:
            raise NoActiveRecordError("No active record; pass record_id")
        return load_record(identifier, config=self.config)

    def _record_show(self, args: _RecordShowArguments) -> dict[str, Any]:
        record = self._active(args.record_id)
        if args.raw:
            values, sources = record.to_dict(), {}
        else:
            resolved = record.resolve(config=self.config)
            values, sources = resolved.values, resolved.sources
        result: dict[str, Any] = {
            "id": record.id,
            "values": json_value(values),
            "sources": json_value(sources),
            "markdown": record.markdown[:MARKDOWN_LIMIT],
        }
        if len(record.markdown) > MARKDOWN_LIMIT:
            result["markdown_truncated"] = True
            result["markdown_resource"] = f"hiraku://records/{record.id}"
        return result

    def _resolve_commit(self, commit: str | None) -> str | None:
        if commit is None:
            return None
        return GitRepository(self.config).resolve(commit)

    def _record_create(self, args: _RecordCreateArguments) -> dict[str, Any]:
        reserved = {"id", "parent_id", "related_to", "markdown", "type", "commit"}
        if reserved.intersection(args.fields):
            raise ValidationError(
                f"Use the dedicated arguments for {sorted(reserved)}, not fields"
            )
        fields = dict(args.fields)
        if args.type is not None:
            fields["type"] = args.type
        commit = self._resolve_commit(args.commit)
        if commit is not None:
            fields["commit"] = commit
        record = Record.create(
            args.record_id,
            parent_id=args.parent_id,
            related_to=list(args.related_to),
            markdown=args.markdown,
            activate=args.activate,
            config=self.config,
            **fields,
        )
        return {"created": record.id, "path": str(record.path)}

    def _record_update(self, args: _RecordUpdateArguments) -> dict[str, Any]:
        changes = dict(args.fields)
        if args.related_to is not None:
            changes["related_to"] = list(args.related_to)
        commit = self._resolve_commit(args.commit)
        if commit is not None:
            changes["commit"] = commit
        record = update_record(
            args.record_id,
            changes=changes,
            unset=args.unset,
            parent_id=args.parent_id,
            make_root=args.make_root,
            markdown=args.markdown,
            config=self.config,
        )
        return {"updated": record.id}

    def _runs(self, args: _RunsArguments) -> list[dict[str, Any]]:
        record = self._active(args.record_id)
        query: dict[str, Any] = {"limit": args.limit}
        if args.sort is not None:
            query["sort"] = args.sort
            if args.ascending is not None:
                query["ascending"] = args.ascending
            else:
                declarations = (
                    record.resolve(config=self.config).values.get("metrics") or []
                )
                directions = {item.name: item.direction for item in declarations}
                query["ascending"] = directions.get(args.sort) != "maximize"
        elif args.ascending is not None:
            raise ValidationError("ascending requires sort")
        runs = record.resolve_tracking_backend(config=self.config).list_runs(
            record_id=None if args.all else record.id, **query
        )
        return json_value(runs)

    # Resources ----------------------------------------------------------------

    async def _list_resources(self, ctx: Any, params: Any) -> types.ListResourcesResult:
        resources: list[types.Resource] = []
        for entry in self.service.catalog.entries.values():
            resources.append(
                types.Resource(
                    name=f"workflow-{entry.id}",
                    uri=f"hiraku://workflows/{entry.id}",
                    description=f"Prompt document of workflow {entry.id}",
                    mime_type="text/markdown",
                )
            )
        resources.extend(
            types.Resource(
                name=item["name"],
                uri=item["uri"],
                description=item["description"],
                mime_type=item["mime_type"],
            )
            for item in self.service.resource_definitions()
        )
        try:
            contexts = await anyio.to_thread.run_sync(
                partial(list_contexts, config=self.config)
            )
        except HirakuError:
            contexts = []
        resources.extend(
            types.Resource(
                name=f"context-{context.id}",
                uri=f"hiraku://contexts/{context.id}",
                description="Curated project context",
                mime_type="text/markdown",
            )
            for context in contexts
        )
        return types.ListResourcesResult(resources=resources)

    async def _list_resource_templates(
        self, ctx: Any, params: Any
    ) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(
                    name="record",
                    uri_template="hiraku://records/{record_id}",
                    description="Full Markdown file of a research record",
                    mime_type="text/markdown",
                ),
                types.ResourceTemplate(
                    name="session",
                    uri_template="hiraku://sessions/{session_id}",
                    description="Full stored state of a workflow session",
                    mime_type="application/json",
                ),
            ]
        )

    async def _read_resource(self, ctx: Any, params: Any) -> types.ReadResourceResult:
        uri = str(params.uri)
        text, mime_type = await anyio.to_thread.run_sync(
            partial(self.read_resource, uri)
        )
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(uri=uri, mime_type=mime_type, text=text)
            ]
        )

    def read_resource(self, uri: str) -> tuple[str, str]:
        """Return the text and MIME type of a Hiraku resource.

        Raises:
            hiraku.errors.ValidationError: If the URI is not a Hiraku resource.
        """
        prefix = "hiraku://"
        kind, _, identifier = uri.removeprefix(prefix).partition("/")
        identifier = unquote(identifier)
        if not uri.startswith(prefix) or not identifier:
            raise ValidationError(f"Unknown resource {uri!r}")
        if kind == "workflows":
            workflow_id, _, name = identifier.partition("/")
            if name:
                return self.service.read_resource(workflow_id, name)
            document = self.service.catalog.prompts.document(workflow_id)
            return document.text, "text/markdown"
        if kind == "contexts":
            context = load_context(identifier, config=self.config)
            assert context.path is not None
            return context.path.read_text(encoding="utf-8"), "text/markdown"
        if kind == "records":
            record = load_record(identifier, config=self.config)
            assert record.path is not None
            return record.path.read_text(encoding="utf-8"), "text/markdown"
        if kind == "sessions":
            return _dumps(self.service.show(identifier)), "application/json"
        raise ValidationError(f"Unknown resource {uri!r}")

    # Prompts ------------------------------------------------------------------

    def _prompt_entries(self) -> list[Any]:
        exposed = set(self.service.exposed_ids())
        return [
            entry
            for entry in self.service.catalog.entries.values()
            if entry.loaded and entry.id in exposed
        ]

    async def _list_prompts(self, ctx: Any, params: Any) -> types.ListPromptsResult:
        prompts = []
        for entry in self._prompt_entries():
            schema = entry.handlers["start"].arguments.model_json_schema()
            required = set(schema.get("required", []))
            prompts.append(
                types.Prompt(
                    name=entry.id,
                    description=f"Start the {entry.id} workflow. "
                    + (
                        entry.description.split("\n\n", 1)[0]
                        if entry.description
                        else ""
                    ),
                    arguments=[
                        types.PromptArgument(
                            name=name,
                            description=_property_description(spec),
                            required=name in required,
                        )
                        for name, spec in schema.get("properties", {}).items()
                    ],
                )
            )
        prompts.extend(
            types.Prompt(
                name=item["name"],
                description=item["description"],
                arguments=[
                    types.PromptArgument(**argument) for argument in item["arguments"]
                ],
            )
            for item in self.service.prompt_definitions()
        )
        return types.ListPromptsResult(prompts=prompts)

    async def _get_prompt(self, ctx: Any, params: Any) -> types.GetPromptResult:
        declared = {item["name"] for item in self.service.prompt_definitions()}
        if params.name in declared:
            text = await anyio.to_thread.run_sync(
                partial(self.service.get_prompt, params.name, params.arguments)
            )
            return types.GetPromptResult(
                description=params.name,
                messages=[
                    types.PromptMessage(
                        role="user", content=types.TextContent(type="text", text=text)
                    )
                ],
            )
        entries = {entry.id: entry for entry in self._prompt_entries()}
        entry = entries.get(params.name)
        if entry is None:
            raise WorkflowError(f"Unknown or inactive workflow prompt {params.name!r}")
        provided = {
            key: value for key, value in (params.arguments or {}).items() if value
        }
        text = (
            f"Start the Hiraku workflow `{entry.id}` by calling the "
            f"`{entry.tool_name('start')}` tool."
        )
        if provided:
            text += (
                " Use these arguments, converting them to the types the tool "
                f"schema expects: {json.dumps(provided)}."
            )
        text += (
            " Confirm the resolved arguments with the user if anything is "
            "ambiguous, then follow the returned instructions."
        )
        return types.GetPromptResult(
            description=f"Start the {entry.id} workflow",
            messages=[
                types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=text)
                )
            ],
        )


def serve(config: HirakuConfig, *, exposed: list[str] | None = None) -> None:
    """Run the Hiraku MCP server over stdio for one project.

    Args:
        config: Project bound at startup.
        exposed: Workflow IDs to expose instead of the configured set.
    """
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="hiraku: %(message)s"
    )
    server = HirakuMCPServer(config, exposed=exposed)
    for entry in server.service.catalog.entries.values():
        if entry.error is not None:
            logger.warning("Workflow %s is unavailable: %s", entry.id, entry.error)
    anyio.run(server.run_stdio)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})
    return schema


def _property_description(spec: dict[str, Any]) -> str | None:
    description = spec.get("description")
    if "default" in spec:
        default = json.dumps(spec["default"])
        description = (
            f"{description} (default {default})"
            if description
            else (f"Default {default}")
        )
    return description


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _version() -> str:
    try:
        return version("hiraku")
    except PackageNotFoundError:  # pragma: no cover - source checkouts
        return "0"
