from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from hiraku import __version__
from hiraku.config import load_config
from hiraku.errors import HirakuError
from hiraku.experiments import create_experiment
from hiraku.initialize import initialize_project
from hiraku.launcher import launch
from hiraku.records import Experiment, list_experiments, load_experiment
from hiraku.run_table import RunCollection, read_runs

app = typer.Typer(
    name="hiraku",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="AI-assisted research experiment workflow toolkit.",
)
experiment_app = typer.Typer(no_args_is_help=True, help="Create and inspect experiments.")
app.add_typer(experiment_app, name="exp")
app.add_typer(experiment_app, name="experiment")
console = Console()
error_console = Console(stderr=True)


def _execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except HirakuError as exc:
        error_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _metadata(experiment: Experiment) -> dict[str, Any]:
    return dict(experiment.metadata)


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the Hiraku version and exit.", is_eager=True),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command("init")
def init_command() -> None:
    """Initialize Hiraku in the current Git repository."""
    targets = _execute(lambda: initialize_project(Path.cwd()))
    console.print("Initialized Hiraku:")
    root_path = Path.cwd().resolve()
    for target in targets:
        console.print(f"  [green]created[/green] {target.relative_to(root_path)}")


@experiment_app.command("create")
def experiment_create(
    experiment_id: Annotated[str, typer.Argument(help="Lowercase experiment slug.")],
    mode: Annotated[
        Literal["guided", "auto"], typer.Option("--mode", help="Agent workflow mode.")
    ] = "guided",
    branch: Annotated[
        str | None, typer.Option("--branch", help="Override the default exp/<id> branch.")
    ] = None,
) -> None:
    """Create an experiment and switch to its branch."""

    def operation() -> Experiment:
        config = load_config()
        return create_experiment(config, experiment_id, mode=mode, branch=branch)

    experiment = _execute(operation)
    console.print(f"Created experiment [bold]{experiment.id}[/bold] on branch {experiment.branch}.")


@experiment_app.command("list")
def experiment_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON metadata.")] = False,
) -> None:
    """List experiment records."""
    experiments = _execute(lambda: list_experiments(load_config()))
    if json_output:
        _json([_metadata(experiment) for experiment in experiments])
        return
    if not experiments:
        console.print("No experiments found.")
        return
    table = Table()
    for column in ("id", "mode", "branch"):
        table.add_column(column)
    for experiment in experiments:
        table.add_row(experiment.id, experiment.mode, experiment.branch)
    console.print(table)


@experiment_app.command("show")
def experiment_show(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON metadata.")] = False,
) -> None:
    """Show an experiment record."""
    experiment = _execute(lambda: load_experiment(load_config(), experiment_id))
    if json_output:
        _json(_metadata(experiment))
    else:
        typer.echo(experiment.raw, nl=False)


@app.command("launch", context_settings={"allow_extra_args": False})
def launch_command(
    target: Annotated[str, typer.Argument(help="Named target: <experiment>:<entrypoint>.")],
) -> None:
    """Launch a named experiment entrypoint."""
    exit_code = _execute(lambda: launch(load_config(), target))
    if exit_code:
        raise typer.Exit(code=exit_code)


def _render_runs(collection: RunCollection) -> None:
    if not collection.rows:
        console.print("No runs found.")
        return
    columns = collection.fieldnames
    if "_rank" in columns:
        columns = ["_rank", *(column for column in columns if column != "_rank")]
    table = Table()
    for column in columns:
        table.add_column(column)
    for row in collection.rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


@app.command("runs")
def runs_command(
    experiment_id: Annotated[str, typer.Argument(help="Experiment ID.")],
    sort_by: Annotated[
        str | None, typer.Option("--sort", help="Numeric column used to rank runs.")
    ] = None,
    ascending: Annotated[bool, typer.Option("--ascending", help="Sort lowest first.")] = False,
    descending: Annotated[bool, typer.Option("--descending", help="Sort highest first.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit a JSON row array.")] = False,
) -> None:
    """List or rank tracked runs for one experiment."""
    if sort_by is None and (ascending or descending):
        _execute(lambda: (_ for _ in ()).throw(HirakuError("Sort direction requires --sort.")))
    if sort_by is not None and ascending == descending:
        _execute(
            lambda: (_ for _ in ()).throw(
                HirakuError("Use exactly one of --ascending or --descending with --sort.")
            )
        )

    def operation() -> RunCollection:
        config = load_config()
        load_experiment(config, experiment_id)
        return read_runs(
            config.table_path,
            experiment_id,
            sort_by=sort_by,
            descending=descending,
        )

    collection = _execute(operation)
    if json_output:
        _json(collection.rows)
    else:
        _render_runs(collection)


def main() -> None:
    app()
