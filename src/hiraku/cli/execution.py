"""Click commands for launching entrypoints and querying tracked runs."""

from __future__ import annotations

import shlex

import click

from ..core.errors import ValidationError
from ..core.launch import _dispatcher, launch, render_arguments
from .cli import CLIContext, _active, _emit_json, _names, _overrides, _table, main


@main.command("launch")
@click.argument("entrypoint_name")
@click.argument("entrypoint_overrides", nargs=-1, type=click.UNPROCESSED)
@click.option("--record", "record_id")
@click.option("--commit")
@click.option("--dispatch")
@click.option("--dry-run", is_flag=True)
@click.pass_obj
def launch_command(
    state: CLIContext,
    entrypoint_name: str,
    entrypoint_overrides: tuple[str, ...],
    record_id: str | None,
    commit: str | None,
    dispatch: str | None,
    dry_run: bool,
) -> None:
    """Launch an effective record entrypoint."""
    record = _active(state.config, record_id)
    entrypoint = record.entrypoint(entrypoint_name, config=state.config)
    overrides = _overrides(entrypoint_overrides)
    if dry_run:
        click.echo(f"Dispatcher: {_dispatcher(state.config, dispatch)}")
        arguments = dict(entrypoint.args)
        arguments.update(overrides)
        command = [*entrypoint.command, *render_arguments(arguments)]
        click.echo(f"Command: {shlex.join(command)}")
        return
    result = launch(
        entrypoint,
        name=entrypoint_name,
        args=overrides,
        record_id=record.id,
        commit=commit,
        dispatch=dispatch,
        check=False,
        config=state.config,
    )
    if result.returncode:
        raise click.exceptions.Exit(result.returncode)


@main.command("runs")
@click.argument("subcommand", required=False, type=click.Choice(["list"]))
@click.option("--record", "record_id")
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
    subcommand: str | None,
    record_id: str | None,
    metrics: str | None,
    params: str | None,
    sort: str | None,
    ascending: bool,
    descending: bool,
    limit: int | None,
    json_output: bool,
) -> None:
    """List runs from the selected record's tracking source."""
    _ = subcommand
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
    runs = record.runs(
        config=state.config,
        **query,
    )
    if json_output:
        _emit_json(runs)
    else:
        columns = ["id", "record", "timestamp", "commit"]
        columns.extend(f"param:{name}" for name in param_names)
        columns.extend(f"metric:{name}" for name in metric_names)
        rows = [
            [
                run.id,
                run.record_id,
                run.timestamp or "",
                run.commit or "",
                *(run.params.get(name) for name in param_names),
                *(run.metrics.get(name) for name in metric_names),
            ]
            for run in runs
        ]
        click.echo(_table(columns, rows))
