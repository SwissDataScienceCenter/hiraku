"""Command-line interface for Hiraku projects."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import click
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rich import box, print_json
from rich.console import Console
from rich.table import Table

from .errors import (
    GitError,
    HirakuError,
    NoActiveRecordError,
    ValidationError,
)
from .execution import execute_command
from .git import GitRepository, init_code_repository, require_git
from .io import dump_yaml
from .project import Project
from .record import Record, validate_identifier
from .tracking import Leaderboard, Run
from .tracking.leaderboard import _leaderboard_path


class CLIContext:
    def __init__(self) -> None:
        self._project: Project | None = None

    @property
    def project(self) -> Project:
        if self._project is None:
            self._project = Project.discover()
        return self._project


class HirakuGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (HirakuError, PydanticValidationError) as exc:
            raise click.ClickException(str(exc)) from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Record):
        return {
            **_json_value(value.metadata),
            "id": value.id,
            "markdown": value.markdown,
        }
    if isinstance(value, Run):
        return {
            "id": value.id,
            "metrics": _json_value(value.metrics),
            "params": _json_value(value.params),
            "record_id": value.record_id,
            "commit": value.commit,
            "timestamp": _json_value(value.timestamp),
            "duration": value.duration,
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


def _emit_json(value: Any, pretty: bool = False) -> None:
    serialized = json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False)
    if not pretty:
        click.echo(serialized)
    else:
        print_json(json=serialized)


def _validate_json_options(json_output: bool, pretty: bool) -> None:
    if pretty and not json_output:
        raise ValidationError("--pretty requires --json")


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


def _active(project: Project, record_id: str | None = None) -> Record:
    identifier = record_id or project.active_record_id
    if identifier is None:
        raise NoActiveRecordError("No active record; supply --record or set one")
    return project.vault.load(identifier)


def _edit(path: Path) -> None:
    editor = shlex.split(os.environ.get("EDITOR", "vim"))
    if not editor:
        raise ValidationError("EDITOR must contain a command")
    result = subprocess.run([*editor, str(path)], check=False)
    if result.returncode:
        raise ValidationError(f"Editor exited with status {result.returncode}")


def _edit_recoverable(path: Path, loader: Callable[[], Any], label: str) -> Any:
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

        server = create_server(Project.discover())
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
    if init_git:
        require_git()
    absolute_root = root.expanduser().absolute()
    markers = [absolute_root / "hiraku.yml"]
    if absolute_root.is_dir():
        markers.extend(
            child / "hiraku.yml"
            for child in absolute_root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
    existing_marker = next((marker for marker in markers if marker.is_file()), None)
    if existing_marker is not None and template is None:
        project = Project.load(existing_marker)
        message = "Hiraku project already initialized"
    else:
        project = Project.create(root, template=template)
        message = "Initialized Hiraku project"
    if init_git:
        init_code_repository(project)
    click.echo(f"{message} at {project.path}")


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
    project = Project.create(
        root,
        example=example,
        results=results,
        with_generator=with_generator,
    )
    if init_git:
        init_code_repository(project)
    click.echo(f"Initialized Hiraku project at {project.path}")
    if generator is not None:
        with as_file(generator) as source:
            try:
                namespace = runpy.run_path(str(source))
                if "generate_results" not in namespace:
                    namespace["generate"](project)
                else:
                    with TemporaryDirectory(prefix="hiraku-generated-") as staging:
                        overlay = Path(staging) / "results"
                        namespace["generate_results"](overlay)
                        generated = Project.load(overlay / "hiraku.yml")
                        shutil.copytree(
                            generated.records_dir,
                            project.records_dir,
                            dirs_exist_ok=True,
                        )
                        for result in overlay.iterdir():
                            if result.is_file() and result.name != "hiraku.yml":
                                shutil.copyfile(result, project.root / result.name)
                        if generated.active_record_id is None:
                            project.clear_active_record()
                        else:
                            project.set_active_record(generated.active_record_id)
            except Exception as exc:
                raise click.ClickException(
                    "Generation failed; initialized project and completed runs kept: "
                    f"{exc}"
                ) from exc


@main.command("status")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def status_command(state: CLIContext, json_output: bool, pretty: bool) -> None:
    """Show project and active record."""
    _validate_json_options(json_output, pretty)
    project = state.project
    try:
        branch = GitRepository(project.root).current_branch
    except GitError:
        branch = None
    result = {
        "project_root": project.root,
        "active_record_id": project.active_record_id,
        "leaderboards": Leaderboard.discover(project),
        "git_branch": branch,
    }
    if json_output:
        _emit_json(result, pretty=pretty)
    else:
        _print_table(("field", "value"), list(result.items()))


@main.group("record")
def record_group() -> None:
    """Manage research records."""


@record_group.command("create")
@click.argument("record_id")
@click.option("--title")
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
    title: str | None,
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
    if any(key in fields for key in ("id", "title", "parent_id", "related_to")):
        raise ValidationError(
            "Use the record_id argument and dedicated metadata options"
        )
    if type is not None:
        fields["type"] = type
    if title is not None:
        fields["title"] = title
    if commit is not None:
        fields["commit"] = GitRepository(state.project.root).resolve(commit)
    record = state.project.vault.create(
        record_id,
        parent_id=parent_id,
        related_to=list(related_to),
        markdown=_markdown(markdown_file),
        **fields,
    )
    if not no_activate:
        state.project.set_active_record(record)
    target = state.project.vault.file_path(record.id)
    click.echo(f"Created a new record {record.id!r} at {target}")


@record_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def record_list(state: CLIContext, json_output: bool, pretty: bool) -> None:
    """List all research records."""
    _validate_json_options(json_output, pretty)
    records = state.project.vault.list()
    if json_output:
        _emit_json(records, pretty=pretty)
    else:
        _print_table(
            ("id", "title", "parent", "type", "state"),
            [(r.id, r.title, r.parent_id, r.type, r.state) for r in records],
        )


@record_group.command("show")
@click.argument("record_id", required=False)
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def record_show(
    state: CLIContext,
    record_id: str | None,
    json_output: bool,
    pretty: bool,
) -> None:
    """Show the document's local fields and Markdown."""
    _validate_json_options(json_output, pretty)
    record = _active(state.project, record_id)
    if json_output:
        _emit_json(record, pretty=pretty)
    else:
        click.echo(f"---\n{dump_yaml(record.metadata)}---\n{record.markdown}", nl=False)


@record_group.command("update")
@click.argument("record_id")
@click.option("--related-to", multiple=True)
@click.option("--commit")
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE")
@click.option("--unset", "unset_values", multiple=True)
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.pass_obj
def record_update(
    state: CLIContext,
    record_id: str,
    related_to: tuple[str, ...],
    commit: str | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
    markdown_file: Path | None,
) -> None:
    """Update stored fields; --unset removes an override."""
    record = state.project.vault.load(record_id)
    changes = _fields(set_values)
    if "markdown" in changes:
        raise ValidationError("Use --markdown-file to update record Markdown")
    if "id" in changes:
        raise ValidationError("id cannot be updated")
    if related_to:
        changes["related_to"] = list(related_to)
    if commit is not None:
        changes["commit"] = GitRepository(state.project.root).resolve(commit)
    for key in unset_values:
        if key == "id":
            raise ValidationError(f"Cannot unset {key}")
        if key in changes:
            raise ValidationError(f"Cannot set and unset {key!r} together")
    state.project.vault.update(
        record,
        changes,
        markdown=_markdown(markdown_file) if markdown_file is not None else None,
        unset=unset_values,
    )
    click.echo(f"Updated record {record_id}")


@record_group.command("edit")
@click.argument("record_id")
@click.pass_obj
def record_edit(state: CLIContext, record_id: str) -> None:
    """Edit a record's stored Markdown file."""
    record = state.project.vault.load(record_id)
    _edit_recoverable(
        state.project.vault.file_path(record.id),
        lambda: state.project.vault.load(record_id),
        f"Record {record_id!r}",
    )
    click.echo(f"Validated record {record_id}")


@record_group.command("delete")
@click.argument("record_id")
@click.pass_obj
def record_delete(state: CLIContext, record_id: str) -> None:
    """Delete the document at the given OKF ID."""
    record = state.project.vault.load(record_id)
    state.project.vault.delete(record_id)
    if state.project.active_record_id == record.id:
        state.project.clear_active_record()
    click.echo(f"Deleted record {record_id}")


@record_group.command("set")
@click.argument("record_id")
@click.pass_obj
def record_set(state: CLIContext, record_id: str) -> None:
    """Set the active record."""
    state.project.set_active_record(record_id)
    click.echo(f"Active record: {record_id}")


@record_group.command("tree")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def record_tree(state: CLIContext, json_output: bool, pretty: bool) -> None:
    """Show the logical parent tree."""
    _validate_json_options(json_output, pretty)
    records = state.project.vault.list()
    if json_output:
        _emit_json(
            [{"id": r.id, "parent_id": r.parent_id} for r in records],
            pretty=pretty,
        )
        return
    children: dict[str | None, list[Record]] = {}
    for record in records:
        children.setdefault(record.parent_id, []).append(record)

    known = {record.id for record in records}
    roots = [record for record in records if record.parent_id not in known]
    seen: set[str] = set()
    # Remaining records seed components without roots, such as cycles.
    stack = [(record, 0) for record in reversed(roots + records)]
    while stack:
        record, depth = stack.pop()
        if record.id in seen:
            continue
        seen.add(record.id)
        click.echo("  " * depth + record.id)
        stack.extend(
            (child, depth + 1) for child in reversed(children.get(record.id, []))
        )


@main.group("workflow")
def workflow_group() -> None:
    """List and run project LangGraph workflows."""


@workflow_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def workflow_list(state: CLIContext, json_output: bool, pretty: bool) -> None:
    """List project LangGraph workflows."""
    _validate_json_options(json_output, pretty)
    from .workflows.langgraph import workflow_details

    workflows = workflow_details(project=state.project)
    if json_output:
        _emit_json(workflows, pretty=pretty)
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

    click.echo(workflow_mermaid(workflow_id, project=state.project), nl=False)


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
@click.option("--pretty", is_flag=True, help="Pretty-print the JSON response.")
@click.pass_obj
def workflow_start(
    state: CLIContext,
    workflow_id: str,
    arguments_file: Any,
    context_file: Any,
    pretty: bool,
) -> None:
    """Start a LangGraph workflow and print its interrupt or result."""
    from .workflows.langgraph import start_workflow

    _emit_json(
        start_workflow(
            workflow_id,
            _arguments(arguments_file),
            project=state.project,
            context=_arguments(context_file, name="context"),
        ),
        pretty=pretty,
    )


@workflow_group.command("sessions")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def workflow_sessions(state: CLIContext, pretty: bool) -> None:
    """List recorded workflow sessions as JSON."""
    from .workflows.session_history import list_sessions

    _emit_json(list_sessions(project=state.project), pretty=pretty)


@workflow_group.command("history")
@click.argument("workflow_session_id")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def workflow_history(state: CLIContext, workflow_session_id: str, pretty: bool) -> None:
    """Show a workflow session's recorded metadata and history."""
    from .workflows.session_history import session_history

    _emit_json(
        session_history(workflow_session_id, project=state.project), pretty=pretty
    )


@workflow_group.command("status")
@click.argument("workflow_session_id")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def workflow_status_command(
    state: CLIContext, workflow_session_id: str, pretty: bool
) -> None:
    """Inspect a checkpointed LangGraph workflow run."""
    from .workflows.langgraph import workflow_status

    _emit_json(
        workflow_status(workflow_session_id, project=state.project), pretty=pretty
    )


@workflow_group.command("resume")
@click.argument("workflow_session_id")
@click.option("--checkpoint-id", required=True, help="Checkpoint ID from status.")
@_ARGUMENTS_FILE
@_CONTEXT_FILE
@click.option("--pretty", is_flag=True, help="Pretty-print the JSON response.")
@click.pass_obj
def workflow_resume_command(
    state: CLIContext,
    workflow_session_id: str,
    checkpoint_id: str,
    arguments_file: Any,
    context_file: Any,
    pretty: bool,
) -> None:
    """Resume a paused LangGraph workflow with a JSON response."""
    from .workflows.langgraph import resume_workflow

    _emit_json(
        resume_workflow(
            workflow_session_id,
            _arguments(arguments_file),
            project=state.project,
            checkpoint_id=checkpoint_id,
            context=_arguments(context_file, name="context"),
        ),
        pretty=pretty,
    )


@main.command("run")
@click.argument("command", required=True, nargs=-1, type=click.UNPROCESSED)
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
        project=state.project,
    )
    code = result.returncode
    raise click.exceptions.Exit(128 - code if code < 0 else code)


@main.group("leaderboard")
def leaderboard_group() -> None:
    """Manage leaderboard definitions and their tracking sources."""


@leaderboard_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.pass_obj
def leaderboard_list(state: CLIContext, json_output: bool, pretty: bool) -> None:
    """List leaderboards, including errors in invalid definitions."""
    _validate_json_options(json_output, pretty)
    rows = []
    for name in Leaderboard.discover(state.project):
        try:
            leaderboard = Leaderboard.load(state.project, name)
            rows.append(
                {"name": name, "source": leaderboard.source.type, "error": None}
            )
        except HirakuError as exc:
            rows.append({"name": name, "source": None, "error": str(exc)})
    if json_output:
        _emit_json(rows, pretty=pretty)
    else:
        _print_table(("name", "source", "error"), [list(row.values()) for row in rows])


@leaderboard_group.command("show")
@click.argument("name")
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True)
@click.pass_obj
def leaderboard_show(
    state: CLIContext, name: str, json_output: bool, pretty: bool
) -> None:
    """Show validated leaderboard configuration without API keys."""
    _validate_json_options(json_output, pretty)
    data = Leaderboard.load(state.project, name).model_dump(
        mode="json", exclude_none=True
    )
    if json_output:
        _emit_json(data, pretty=pretty)
    else:
        click.echo(dump_yaml(data), nl=False)


@leaderboard_group.command("create")
@click.argument("name")
@click.option(
    "--source",
    "source_type",
    type=click.Choice(["csv", "mlflow", "wandb"]),
    required=True,
)
@click.option(
    "--path",
    type=click.Path(path_type=Path),
    help="CSV file, relative to project root.",
)
@click.option("--tracking-uri", help="MLflow tracking server URI.")
@click.option(
    "--database", type=click.Path(path_type=Path), help="Local MLflow database."
)
@click.option("--experiment-name", help="MLflow experiment name.")
@click.option("--entity", help="W&B account or team.")
@click.option("--project", help="W&B project name.")
@click.pass_obj
def leaderboard_create(
    state: CLIContext, name: str, source_type: str, **options: Any
) -> None:
    """Create a leaderboard; edit its YAML to declare comparison metrics."""
    source = {
        "type": source_type,
        **{key: value for key, value in options.items() if value is not None},
    }
    Leaderboard.create(state.project, name, source)
    click.echo(f"Created leaderboard {name}")


@leaderboard_group.command("edit")
@click.argument("name")
@click.pass_obj
def leaderboard_edit(state: CLIContext, name: str) -> None:
    """Edit a definition and validate it after the editor exits."""
    _edit_recoverable(
        _leaderboard_path(state.project, name),
        lambda: Leaderboard.load(state.project, name),
        f"Leaderboard {name!r}",
    )
    click.echo(f"Validated leaderboard {name}")


@leaderboard_group.command("delete")
@click.argument("name")
@click.pass_obj
def leaderboard_delete(state: CLIContext, name: str) -> None:
    """Delete a definition, leaving its tracked runs untouched."""
    Leaderboard.delete(state.project, name)
    click.echo(f"Deleted leaderboard {name}")


@main.command("runs")
@click.option("--record", "record_id")
@click.option("--leaderboard", "leaderboard_name")
@click.option("--metrics")
@click.option("--params")
@click.option("--sort")
@click.option("--ascending", is_flag=True)
@click.option("--descending", is_flag=True)
@click.option("--limit", type=click.IntRange(min=0))
@click.option("--json", "json_output", is_flag=True)
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output.")
@click.pass_obj
def run_list(
    state: CLIContext,
    record_id: str | None,
    leaderboard_name: str | None,
    metrics: str | None,
    params: str | None,
    sort: str | None,
    ascending: bool,
    descending: bool,
    limit: int | None,
    json_output: bool,
    pretty: bool,
) -> None:
    """List all runs in a leaderboard, optionally filtered by record ID."""
    _validate_json_options(json_output, pretty)
    if ascending and descending:
        raise ValidationError("--ascending and --descending are mutually exclusive")
    if (ascending or descending) and sort is None:
        raise ValidationError("A sort direction requires --sort")
    leaderboard = Leaderboard.load(state.project, leaderboard_name)
    runs = leaderboard.list_runs(
        state.project,
        record_id=record_id,
        sort=sort,
        ascending=(ascending if ascending or descending else None),
        limit=limit,
    )
    metric_names = (
        _names(metrics, "metric")
        if metrics is not None
        else leaderboard.metric_names(runs)
    )
    param_names = (
        _names(params, "parameter")
        if params is not None
        else list(dict.fromkeys(name for run in runs for name in run.params))
    )
    if json_output:
        _emit_json(runs, pretty=pretty)
    else:
        record_names = {
            item.id: item.title or item.id for item in state.project.vault.list()
        }
        columns = ["id", "record", "timestamp", "duration", "commit"]
        columns.extend(f"param:{name}" for name in param_names)
        columns.extend(metric_names)
        rows = [
            [
                run.id,
                record_names.get(run.record_id, run.record_id),
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
@click.option(
    "--leaderboard", "leaderboard_name", help="Leaderboard selected when opening."
)
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
    leaderboard_name: str | None,
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
        click.echo(f"Created dashboard: {init_dashboard(state.project)}")
        if not edit:
            return
    exit_code = serve_dashboard(
        state.project,
        leaderboard_name=leaderboard_name,
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
