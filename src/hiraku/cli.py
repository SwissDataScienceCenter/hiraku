"""Command-line interface for Hiraku projects."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import click
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rich import box
from rich.console import Console
from rich.table import Table

from .config import HirakuConfig
from .errors import GitError, HirakuError, NoActiveRecordError, ValidationError
from .execution import execute_command
from .git import GitRepository, init_code_repository, require_git
from .io import dump_yaml
from .record import Record, list_records, load_record
from .run import Run
from .validation import validate_identifier


class CLIContext:
    def __init__(self) -> None:
        self._config: HirakuConfig | None = None

    @property
    def config(self) -> HirakuConfig:
        if self._config is None:
            self._config = HirakuConfig.discover()
        return self._config


class HirakuGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (HirakuError, PydanticValidationError) as exc:
            raise click.ClickException(str(exc)) from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Record):
        return {**_json_value(value.to_dict()), "markdown": value.markdown}
    if isinstance(value, Run):
        return {
            "id": value.id,
            "metrics": _json_value(value.metrics),
            "params": _json_value(value.params),
            "record_id": value.record_id,
            "commit": value.commit,
            "timestamp": _json_value(value.timestamp),
            "duration": value.duration,
            "entrypoint": value.entrypoint,
            "url": value.url,
            "raw": _json_value(value.raw),
        }
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump())
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit_json(value: Any) -> None:
    click.echo(json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False))


def _print_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    table = Table(box=box.ROUNDED, header_style="bold cyan", pad_edge=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*("" if item is None else str(item) for item in row))
    console = Console()
    console.print(table)


def _fields(values: Sequence[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for expression in values:
        key, separator, raw = expression.partition("=")
        if not separator or not key:
            raise ValidationError("--set requires KEY=VALUE")
        if key in fields:
            raise ValidationError(f"Field {key!r} was supplied more than once")
        try:
            fields[key] = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML for {key!r}: {exc}") from exc
    return fields


def _names(raw: str | None, label: str) -> list[str]:
    if raw is None:
        return []
    names = [item.strip() for item in raw.split(",")]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValidationError(f"Invalid {label} names")
    return names


def _markdown(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc}") from exc


def _active(config: HirakuConfig, record_id: str | None = None) -> Record:
    identifier = record_id or config.active_record_id
    if identifier is None:
        raise NoActiveRecordError("No active record; supply --record or set one")
    return load_record(identifier, config=config)


def _edit(path: Path) -> None:
    editor = shlex.split(os.environ.get("EDITOR", "vim"))
    if not editor:
        raise ValidationError("EDITOR must contain a command")
    result = subprocess.run([*editor, str(path)], check=False)
    if result.returncode:
        raise ValidationError(f"Editor exited with status {result.returncode}")


def _edit_recoverable(path: Path, loader: Callable[[], Record], label: str) -> Record:
    try:
        loader()
    except (ValidationError, PydanticValidationError) as exc:
        if not path.is_file():
            raise
        click.echo(
            f"Warning: {label} is invalid: {exc}\nOpening {path} for recovery.",
            err=True,
        )
    _edit(path)
    return loader()


def _overrides(tokens: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--") or token == "--":
            raise ValidationError(f"Invalid entrypoint argument: {token!r}")
        key, separator, raw = token[2:].partition("=")
        if not key:
            raise ValidationError("Entrypoint argument name must be non-empty")
        if not separator:
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                index += 1
                raw = tokens[index]
            else:
                raw = True
        key = key.replace("-", "_")
        if key in result:
            previous = result[key]
            result[key] = (
                [*previous, raw] if isinstance(previous, list) else [previous, raw]
            )
        else:
            result[key] = raw
        index += 1
    return result


@click.group(name="hiraku", cls=HirakuGroup)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Manage research records, launches, and tracked runs."""
    ctx.ensure_object(CLIContext)


@main.command("mcp")
def mcp_command() -> None:
    """Serve the Hiraku project discovered from the current directory over stdio."""
    try:
        from .mcp import create_server

        server = create_server(HirakuConfig.discover())
    except ModuleNotFoundError as exc:
        if exc.name == "mcp" or (exc.name or "").startswith("mcp."):
            raise click.ClickException(
                "MCP support requires the MCP SDK dependency to be installed"
            ) from exc
        raise
    server.run(transport="stdio")


@main.command("init")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--template", type=click.Path(path_type=Path))
@click.option(
    "--git", "init_git", is_flag=True, help="Initialize the code Git repository."
)
def init_command(
    path: Path | None,
    template: Path | None,
    init_git: bool,
) -> None:
    """Initialize PATH (default: current directory)."""
    root = path if path is not None else Path.cwd()
    marker = root.expanduser().absolute() / "hiraku.yml"
    if init_git:
        require_git()
    if marker.is_file() and template is None:
        config = HirakuConfig.load(marker)
        message = "Hiraku project already initialized"
    else:
        config = HirakuConfig.init(root, template=template)
        message = "Initialized Hiraku project"
    if init_git:
        init_code_repository(config)
    click.echo(f"{message} at {config.path}")


@main.group("example")
def example_group() -> None:
    """List and create bundled examples."""


@example_group.command("list")
def example_list_command() -> None:
    """List available bundled examples."""
    examples = files("hiraku.resources") / "examples"
    rows = []
    for example in sorted(examples.iterdir(), key=lambda item: item.name):
        manifest = example / "example.yml"
        if example.is_dir() and manifest.is_file():
            metadata = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            rows.append((example.name, metadata.get("description", "")))
    _print_table(("name", "description"), rows)


@example_group.command("create")
@click.argument("example")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--with-generator", is_flag=True, help="Copy the example generator.")
@click.option(
    "--generate", is_flag=True, help="Generate example runs after initialization."
)
@click.option(
    "--with-results",
    "results",
    is_flag=True,
    help="Copy bundled research records and tracked runs.",
)
@click.option(
    "--git", "init_git", is_flag=True, help="Initialize the code Git repository."
)
def example_create_command(
    example: str,
    path: Path | None,
    results: bool,
    with_generator: bool,
    generate: bool,
    init_git: bool,
) -> None:
    """Create PATH from a bundled EXAMPLE (default: ./EXAMPLE).

    Copies example files without Git history or network access.
    """
    if generate and results:
        raise click.UsageError("--generate cannot use --with-results")
    validate_identifier(example, label="example name")
    root = path if path is not None else Path(example)
    if init_git:
        require_git()
    generator = None
    if generate:
        generator = (
            files("hiraku.resources") / "examples" / example / "generate_results.py"
        )
        if not generator.is_file():
            raise click.UsageError(f"Example {example!r} has no generator")
    config = HirakuConfig.init(
        root,
        example=example,
        results=results,
        with_generator=with_generator,
    )
    if init_git:
        init_code_repository(config)
    click.echo(f"Initialized Hiraku project at {config.path}")
    if generator is not None:
        with as_file(generator) as source:
            try:
                namespace = runpy.run_path(str(source))
                if "generate_results" not in namespace:
                    namespace["generate"](config)
                else:
                    with TemporaryDirectory(prefix="hiraku-generated-") as staging:
                        overlay = Path(staging) / "results"
                        namespace["generate_results"](overlay)
                        generated = HirakuConfig.load(overlay / "hiraku.yml")
                        shutil.copytree(
                            generated.records_dir,
                            config.records_dir,
                            dirs_exist_ok=True,
                        )
                        for result in overlay.iterdir():
                            if result.is_file() and result.name != "hiraku.yml":
                                shutil.copyfile(result, config.root / result.name)
                        config.active_record_id = generated.active_record_id
                        config.save()
            except Exception as exc:
                raise click.ClickException(
                    "Generation failed; initialized project and completed runs kept: "
                    f"{exc}"
                ) from exc


@main.command("status")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def status_command(state: CLIContext, json_output: bool) -> None:
    """Show project and active record."""
    config = state.config
    try:
        branch = GitRepository(config.root).current_branch
    except GitError:
        branch = None
    tracking_backend = config.tracking_backend
    if isinstance(tracking_backend, str):
        tracking_backend = {"type": tracking_backend}
    result = {
        "project_root": config.root,
        "active_record_id": config.active_record_id,
        "tracking_backend": tracking_backend,
        "git_branch": branch,
    }
    if json_output:
        _emit_json(result)
    else:
        _print_table(("field", "value"), list(result.items()))


@main.group("record")
def record_group() -> None:
    """Manage research records."""


@record_group.command("create")
@click.argument("record_id")
@click.option("--parent", "parent_id")
@click.option("--related-to", multiple=True)
@click.option("--type")
@click.option("--commit")
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE")
@click.option("--no-activate", is_flag=True)
@click.pass_obj
def record_create(
    state: CLIContext,
    record_id: str,
    parent_id: str | None,
    related_to: tuple[str, ...],
    type: str | None,
    commit: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    no_activate: bool,
) -> None:
    """Create a root or child record."""
    fields = _fields(set_values)
    if any(key in fields for key in ("id", "parent_id", "related_to")):
        raise ValidationError("Use dedicated options for id, parent_id and related_to")
    if type is not None:
        fields["type"] = type
    if commit is not None:
        fields["commit"] = GitRepository(state.config.root).resolve(commit)
    record = Record.create(
        record_id,
        parent_id=parent_id,
        related_to=list(related_to),
        markdown=_markdown(markdown_file),
        activate=not no_activate,
        config=state.config,
        **fields,
    )
    click.echo(f"Created record {record.id} at {record.path}")


@record_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_list(state: CLIContext, json_output: bool) -> None:
    """List all research records."""
    records = list_records(config=state.config)
    if json_output:
        _emit_json(records)
    else:
        _print_table(
            ("id", "parent", "type", "status"),
            [(r.id, r.parent_id, r.type, r.status) for r in records],
        )


@record_group.command("show")
@click.argument("record_id", required=False)
@click.option("--raw", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_show(
    state: CLIContext, record_id: str | None, raw: bool, json_output: bool
) -> None:
    """Show stored or effective fields with their sources."""
    record = _active(state.config, record_id)
    if raw:
        values = record.to_dict()
        sources = {}
    else:
        resolved = record.resolve(config=state.config)
        values, sources = resolved.values, resolved.sources
    if json_output:
        _emit_json({"values": values, "sources": sources, "markdown": record.markdown})
    else:
        click.echo(
            f"---\n{dump_yaml(_json_value(values))}---\n{record.markdown}", nl=False
        )
        if sources:
            click.echo("\nSources:\n" + dump_yaml(sources), nl=False)


@record_group.command("update")
@click.argument("record_id")
@click.option("--parent", "parent_id")
@click.option("--root", "make_root", is_flag=True)
@click.option("--related-to", multiple=True)
@click.option("--commit")
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE")
@click.option("--unset", "unset_values", multiple=True)
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.pass_obj
def record_update(
    state: CLIContext,
    record_id: str,
    parent_id: str | None,
    make_root: bool,
    related_to: tuple[str, ...],
    commit: str | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
    markdown_file: Path | None,
) -> None:
    """Update stored fields; --unset removes an override."""
    record = load_record(record_id, config=state.config)
    if parent_id is not None and make_root:
        raise ValidationError("--parent and --root are mutually exclusive")
    changes = _fields(set_values)
    if "markdown" in changes:
        raise ValidationError("Use --markdown-file to update record Markdown")
    if any(key in changes for key in ("id", "parent_id")):
        raise ValidationError("id and parent_id require dedicated operations")
    if related_to:
        changes["related_to"] = list(related_to)
    if commit is not None:
        changes["commit"] = GitRepository(state.config.root).resolve(commit)
    fields = record.to_dict()
    fields.update(changes)
    for key in unset_values:
        if key in {"id", "parent_id"}:
            raise ValidationError(f"Cannot unset {key}")
        if key in changes:
            raise ValidationError(f"Cannot set and unset {key!r} together")
        fields.pop(key, None)
    new_parent = None if make_root else parent_id
    if make_root or parent_id is not None:
        fields["parent_id"] = new_parent
    body = _markdown(markdown_file) if markdown_file is not None else record.markdown
    candidate = Record.model_validate({**fields, "markdown": body})
    candidate.path = record.path
    candidate._persisted_id = record.id
    if make_root or parent_id is not None:
        candidate.reparent(new_parent, config=state.config)
    else:
        candidate.save()
    click.echo(f"Updated record {record_id}")


@record_group.command("edit")
@click.argument("record_id")
@click.pass_obj
def record_edit(state: CLIContext, record_id: str) -> None:
    """Edit a record's stored Markdown file."""
    validate_identifier(record_id)
    paths = list(state.config.records_dir.glob(f"*/{record_id}.md"))
    if len(paths) != 1:
        raise ValidationError(f"Expected one record path for {record_id!r}")
    _edit_recoverable(
        paths[0],
        lambda: load_record(record_id, config=state.config),
        f"Record {record_id!r}",
    )
    click.echo(f"Validated record {record_id}")


@record_group.command("delete")
@click.argument("record_id")
@click.pass_obj
def record_delete(state: CLIContext, record_id: str) -> None:
    """Delete a leaf record."""
    record = load_record(record_id, config=state.config)
    parent = record.path.parent
    record.delete(config=state.config)
    if state.config.active_record_id == record_id:
        state.config.clear_active_record()
    with suppress(OSError):
        parent.rmdir()
    click.echo(f"Deleted record {record_id}")


@record_group.command("set")
@click.argument("record_id")
@click.pass_obj
def record_set(state: CLIContext, record_id: str) -> None:
    """Set the active record."""
    state.config.set_active_record(record_id)
    click.echo(f"Active record: {record_id}")


@record_group.command("tree")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_tree(state: CLIContext, json_output: bool) -> None:
    """Show the logical parent tree."""
    records = list_records(config=state.config)
    if json_output:
        _emit_json([{"id": r.id, "parent_id": r.parent_id} for r in records])
        return
    children: dict[str | None, list[Record]] = {}
    for record in records:
        children.setdefault(record.parent_id, []).append(record)

    def visit(parent: str | None, depth: int) -> None:
        for child in children.get(parent, []):
            click.echo("  " * depth + child.id)
            visit(child.id, depth + 1)

    visit(None, 0)


@main.group("workflow")
def workflow_group() -> None:
    """List and run project LangGraph workflows."""


@workflow_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def workflow_list(state: CLIContext, json_output: bool) -> None:
    """List project LangGraph workflows."""
    from .workflows.langgraph import workflow_details

    workflows = workflow_details(config=state.config)
    if json_output:
        _emit_json(workflows)
        return
    _print_table(
        ("id", "description"),
        [
            (workflow["id"], (workflow["description"] or "").split("\n", 1)[0])
            for workflow in workflows
        ],
    )


@workflow_group.command("graph")
@click.argument("workflow_id")
@click.pass_obj
def workflow_graph_command(state: CLIContext, workflow_id: str) -> None:
    """Print a workflow's LangGraph topology as a Mermaid diagram."""
    from .workflows.langgraph import workflow_mermaid

    click.echo(workflow_mermaid(workflow_id, config=state.config), nl=False)


_ARGUMENTS_FILE = click.option(
    "--arguments-file",
    type=click.File("r", encoding="utf-8"),
    help="JSON object with the arguments; use - for standard input.",
)
_CONTEXT_FILE = click.option(
    "--context-file",
    type=click.File("r", encoding="utf-8"),
    help="JSON object with session or transition context; use - for standard input.",
)


def _arguments(arguments_file: Any, *, name: str = "arguments") -> dict[str, Any]:
    if arguments_file is None:
        return {}
    try:
        arguments = json.load(arguments_file)
    except ValueError as exc:
        raise ValidationError(f"Cannot read {name}: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ValidationError(f"{name.capitalize()} must be a JSON object")
    return arguments


@workflow_group.command("start")
@click.argument("workflow_id")
@_ARGUMENTS_FILE
@_CONTEXT_FILE
@click.pass_obj
def workflow_start(
    state: CLIContext, workflow_id: str, arguments_file: Any, context_file: Any
) -> None:
    """Start a LangGraph workflow and print its interrupt or result."""
    from .workflows.langgraph import start_workflow

    _emit_json(
        start_workflow(
            workflow_id,
            _arguments(arguments_file),
            config=state.config,
            context=_arguments(context_file, name="context"),
        )
    )


@workflow_group.command("sessions")
@click.pass_obj
def workflow_sessions(state: CLIContext) -> None:
    """List recorded workflow sessions as JSON."""
    from .workflows.session_history import list_sessions

    _emit_json(list_sessions(config=state.config))


@workflow_group.command("history")
@click.argument("workflow_session_id")
@click.pass_obj
def workflow_history(state: CLIContext, workflow_session_id: str) -> None:
    """Show a workflow session's recorded metadata and history."""
    from .workflows.session_history import session_history

    _emit_json(session_history(workflow_session_id, config=state.config))


@workflow_group.command("status")
@click.argument("workflow_session_id")
@click.pass_obj
def workflow_status_command(state: CLIContext, workflow_session_id: str) -> None:
    """Inspect a checkpointed LangGraph workflow run."""
    from .workflows.langgraph import workflow_status

    _emit_json(workflow_status(workflow_session_id, config=state.config))


@workflow_group.command("resume")
@click.argument("workflow_session_id")
@click.option("--checkpoint-id", required=True, help="Checkpoint ID from status.")
@_ARGUMENTS_FILE
@_CONTEXT_FILE
@click.pass_obj
def workflow_resume_command(
    state: CLIContext,
    workflow_session_id: str,
    checkpoint_id: str,
    arguments_file: Any,
    context_file: Any,
) -> None:
    """Resume a paused LangGraph workflow with a JSON response."""
    from .workflows.langgraph import resume_workflow

    _emit_json(
        resume_workflow(
            workflow_session_id,
            _arguments(arguments_file),
            config=state.config,
            checkpoint_id=checkpoint_id,
            context=_arguments(context_file, name="context"),
        )
    )


@main.command("run")
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
@click.option("--record", "record_id")
@click.option(
    "--dispatch", help="Dispatcher name from the project's dispatch directory."
)
@click.pass_context
def run_command(
    context: click.Context,
    command: tuple[str, ...],
    record_id: str | None,
    dispatch: str | None,
) -> None:
    """Run a project command in the foreground."""
    state: CLIContext = context.obj
    result = execute_command(
        command,
        record_id=record_id,
        dispatch=dispatch,
        config=state.config,
    )
    code = result.returncode
    raise click.exceptions.Exit(128 - code if code < 0 else code)


@main.command("runs")
@click.option("--record", "record_id")
@click.option(
    "--all",
    "all_records",
    is_flag=True,
    help="List all attributed runs from the record's tracking source.",
)
@click.option("--metrics")
@click.option("--params")
@click.option("--sort")
@click.option("--ascending", is_flag=True)
@click.option("--descending", is_flag=True)
@click.option("--limit", type=click.IntRange(min=0))
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def run_list(
    state: CLIContext,
    record_id: str | None,
    all_records: bool,
    metrics: str | None,
    params: str | None,
    sort: str | None,
    ascending: bool,
    descending: bool,
    limit: int | None,
    json_output: bool,
) -> None:
    """List runs attributed to the selected record.

    Use --all for every attributed run in the record's tracking source.
    """
    if ascending and descending:
        raise ValidationError("--ascending and --descending are mutually exclusive")
    if (ascending or descending) and sort is None:
        raise ValidationError("A sort direction requires --sort")
    record = _active(state.config, record_id)
    declarations = record.resolve(config=state.config).values.get("metrics") or []
    metric_names = (
        _names(metrics, "metric")
        if metrics is not None
        else [item.name for item in declarations]
    )
    param_names = _names(params, "parameter")
    query = {
        key: value
        for key, value in {"sort": sort, "limit": limit}.items()
        if value is not None
    }
    if sort is not None:
        directions = {item.name: item.direction for item in declarations}
        query["ascending"] = (
            ascending if ascending or descending else directions.get(sort) != "maximize"
        )
    runs = record.resolve_tracking_backend(config=state.config).list_runs(
        record_id=None if all_records else record.id,
        **query,
    )
    if json_output:
        _emit_json(runs)
    else:
        columns = ["id", "record", "timestamp", "duration", "commit"]
        columns.extend(f"param:{name}" for name in param_names)
        columns.extend(f"metric:{name}" for name in metric_names)
        rows = [
            [
                run.id,
                run.record_id,
                run.timestamp or "",
                run.duration,
                run.commit or "",
                *(run.params.get(name) for name in param_names),
                *(run.metrics.get(name) for name in metric_names),
            ]
            for run in runs
        ]
        _print_table(columns, rows)


@main.command("dashboard")
@click.option("--record", "record_id", help="Record selected when the page opens.")
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    help="Use this port; otherwise automatically choose a free port from 2718.",
)
@click.option("--no-browser", is_flag=True, help="Do not open a browser window.")
@click.option(
    "--init",
    "initialize",
    is_flag=True,
    help="Copy the dashboard notebook into the project; exit unless --edit is given.",
)
@click.option(
    "--edit", is_flag=True, help="Open the project's notebook in marimo-studio."
)
@click.option(
    "--include-code",
    is_flag=True,
    help="Let the read-only dashboard show its notebook code.",
)
@click.pass_obj
def dashboard_command(
    state: CLIContext,
    record_id: str | None,
    port: int | None,
    no_browser: bool,
    initialize: bool,
    edit: bool,
    include_code: bool,
) -> None:
    """Browse records and tracked runs in a local, read-only dashboard."""
    from .dashboard.launcher import init_dashboard, serve_dashboard

    if initialize:
        click.echo(f"Created dashboard: {init_dashboard(state.config)}")
        if not edit:
            return
    exit_code = serve_dashboard(
        state.config,
        record_id=record_id,
        port=port,
        no_browser=no_browser,
        edit=edit,
        include_code=include_code,
    )
    if exit_code:
        raise click.exceptions.Exit(exit_code)


if __name__ == "__main__":
    main()
