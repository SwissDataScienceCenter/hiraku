from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .app import HirakuApp, build_app
from .auto import AutoService, parse_duration
from .errors import HirakuError
from .initialize import initialize
from .services import split_experiment_ref
from .tui import run_tui

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
goal_app = typer.Typer(no_args_is_help=True)
experiment_app = typer.Typer(no_args_is_help=True)
run_app = typer.Typer(no_args_is_help=True)
auto_app = typer.Typer(no_args_is_help=True)
proposal_app = typer.Typer(no_args_is_help=True)
report_app = typer.Typer(no_args_is_help=True)
app.add_typer(goal_app, name="goal")
app.add_typer(experiment_app, name="experiment")
app.add_typer(run_app, name="run")
app.add_typer(auto_app, name="auto")
auto_app.add_typer(proposal_app, name="proposal")
app.add_typer(report_app, name="report")
console = Console()
error_console = Console(stderr=True)


def execute(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except HirakuError as exc:
        error_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc


def output(value: Any, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(value))
    else:
        console.print(value)


def core() -> HirakuApp:
    return build_app()


@app.command("init")
def init_command() -> None:
    """Initialize Hiraku in the current Git repository."""
    created = execute(lambda: initialize(Path.cwd()))
    console.print("[green]Initialized Hiraku.[/green]")
    for path in created:
        console.print(f"  created {path.relative_to(Path.cwd())}")


@goal_app.command("create")
def goal_create(
    goal_id: Annotated[str, typer.Argument()],
    metric: Annotated[str, typer.Option("--metric")],
    direction: Annotated[str, typer.Option("--direction")],
    launch: Annotated[Path, typer.Option("--launch")],
    title: Annotated[str | None, typer.Option()] = None,
    objective: Annotated[str, typer.Option()] = "",
    target: Annotated[float | None, typer.Option()] = None,
    protocol: Annotated[str, typer.Option("--protocol")] = "",
    constraint: Annotated[list[str] | None, typer.Option("--constraint")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a Goal and import its launch manifest."""
    result = execute(
        lambda: core().goals.create(
            goal_id,
            metric,
            direction,
            launch,
            title=title,
            objective=objective,
            target=target,
            evaluation_protocol=protocol,
            constraints=constraint or [],
        ).to_dict()
    )
    output(result, json_output)


@goal_app.command("list")
def goal_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    values = execute(lambda: [goal.to_dict() for goal in core().goals.list()])
    if json_output:
        output(values, True)
        return
    table = Table("Goal", "Metric", "Direction", "Baseline", "Status")
    for goal in values:
        table.add_row(
            goal["id"], goal["metric"], goal["direction"], goal["baseline_commit"][:12], goal["status"]
        )
    console.print(table)


@goal_app.command("show")
def goal_show(
    goal_id: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    value = execute(lambda: core().goals.get(goal_id).to_dict())
    output(value if json_output else json.dumps(value, indent=2), json_output)


@goal_app.command("update")
def goal_update(
    goal_id: str,
    title: Annotated[str | None, typer.Option()] = None,
    objective: Annotated[str | None, typer.Option()] = None,
    protocol: Annotated[str | None, typer.Option("--protocol")] = None,
    constraint: Annotated[list[str] | None, typer.Option("--constraint")] = None,
    launch: Annotated[Path | None, typer.Option("--launch")] = None,
    metric: Annotated[str | None, typer.Option()] = None,
    direction: Annotated[str | None, typer.Option()] = None,
) -> None:
    value = execute(
        lambda: core().goals.update(
            goal_id,
            title=title,
            objective=objective,
            evaluation_protocol=protocol,
            constraints=constraint,
            launch_path=launch,
            metric=metric,
            direction=direction,
        ).to_dict()
    )
    output(value, True)


@experiment_app.command("create")
def experiment_create(
    reference: str,
    hypothesis: Annotated[str, typer.Option("--hypothesis")],
    kind: Annotated[str, typer.Option()] = "hypothesis",
    parent: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    value = execute(
        lambda: core().experiments.create(
            reference, hypothesis, kind=kind, parent=parent, origin="guided"
        ).to_dict()
    )
    if json_output:
        output(value, True)
    else:
        console.print(
            f"[green]Created Experiment {value['goal_id']}/{value['id']}.[/green]\n"
            f"Checked out [bold]{value['branch']}[/bold] from {value['parent_commit'][:12]}."
        )


@experiment_app.command("list")
def experiment_list(
    goal_id: Annotated[str | None, typer.Argument()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    values = execute(lambda: [item.to_dict() for item in core().experiments.list(goal_id)])
    if json_output:
        output(values, True)
        return
    table = Table("Goal/Experiment", "Kind", "Parent", "Status", "Verdict", "Origin")
    for item in values:
        table.add_row(
            f"{item['goal_id']}/{item['id']}", item["kind"], item["parent_id"] or "—",
            item["status"], item["verdict"] or "—", item["origin"],
        )
    console.print(table)


@experiment_app.command("show")
def experiment_show(
    reference: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    value = execute(lambda: core().experiments.get(reference).to_dict())
    output(value if json_output else json.dumps(value, indent=2), json_output)


@experiment_app.command("tree")
def experiment_tree(
    goal_id: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    forest = execute(lambda: core().lineage.forest(goal_id))
    if json_output:
        output(forest, True)
        return

    def print_nodes(nodes: list[dict[str, Any]], prefix: str = "") -> None:
        for index, node in enumerate(nodes):
            last = index == len(nodes) - 1
            console.print(
                escape(
                    f"{prefix}{'└──' if last else '├──'} {node['id']} "
                    f"[{node['kind']}, {node['status']}"
                    f"{', ' + node['verdict'] if node['verdict'] else ''}]"
                )
            )
            print_nodes(node["children"], prefix + ("    " if last else "│   "))

    console.print(f"[bold]{goal_id}[/bold]")
    print_nodes(forest)


@experiment_app.command("conclude")
def experiment_conclude(reference: str, verdict: Annotated[str, typer.Option("--verdict")]) -> None:
    value = execute(lambda: core().experiments.conclude(reference, verdict).to_dict())
    output(value, True)


@experiment_app.command("abandon")
def experiment_abandon(reference: str) -> None:
    value = execute(lambda: core().experiments.abandon(reference).to_dict())
    output(value, True)


@app.command("launch")
def launch_command(
    target: str,
    commit: Annotated[str | None, typer.Option("--commit")] = None,
) -> None:
    run_id, exit_code = execute(lambda: core().launcher.launch(target, commit))
    console.print(f"Run {run_id}: exit {exit_code}")
    if exit_code:
        raise typer.Exit(exit_code)


def _parse_assignments(values: list[str] | None, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        if "=" not in value or not value.split("=", 1)[0]:
            raise HirakuError(f"{label} must be KEY=VALUE: {value}")
        key, item = value.split("=", 1)
        result[key] = item
    return result


@run_app.command("log")
def run_log(
    metric: Annotated[list[str] | None, typer.Option("--metric")] = None,
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    active = run_id or os.environ.get("HIRAKU_RUN_ID")
    if not active:
        execute(lambda: (_ for _ in ()).throw(HirakuError("No active Run; pass --run-id.")))
    execute(
        lambda: core().runs.log(
            active or "", _parse_assignments(param, "Parameter"), _parse_assignments(metric, "Metric")
        )
    )
    console.print(f"Logged values for {active}.")


@app.command("runs")
def runs_command(
    target: str,
    sort: Annotated[str | None, typer.Option("--sort")] = None,
    ascending: Annotated[bool, typer.Option("--ascending")] = False,
    descending: Annotated[bool, typer.Option("--descending")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    csv_path: Annotated[Path | None, typer.Option("--csv")] = None,
) -> None:
    def operation() -> list[dict[str, Any]]:
        if "/" in target:
            goal_id, experiment_id = split_experiment_ref(target)
        else:
            goal_id, experiment_id = target, None
        application = core()
        application.goals.get(goal_id)
        if experiment_id:
            application.experiments.get(target)
        rows = application.runs.rows(goal_id, experiment_id)
        if sort:
            if ascending == descending:
                raise HirakuError("With --sort, choose exactly one of --ascending or --descending.")
            rows = application.runs.sort_rows(rows, sort, descending)
        elif ascending or descending:
            raise HirakuError("--ascending and --descending require --sort.")
        return rows

    rows = execute(operation)
    if csv_path:
        fields = sorted({key for row in rows for key in row if key != "provenance"})
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in fields} for row in rows)
    if json_output:
        output(rows, True)
        return
    fields = ["_rank", "id", "experiment_id", "entrypoint", "status"]
    dynamic = sorted({key for row in rows for key in row if key not in fields and key not in {
        "goal_id", "commit_sha", "exit_code", "provenance", "started_at", "finished_at"
    }})
    shown = [field for field in fields if any(field in row for row in rows)] + dynamic
    table = Table(*shown)
    for row in rows:
        table.add_row(*(str(row.get(field, "—")) if row.get(field) is not None else "—" for field in shown))
    console.print(table)


@proposal_app.command("discover")
def auto_proposal_discover(goal_id: str) -> None:
    value = execute(lambda: AutoService(core()).discover(goal_id))
    output(value, True)


@proposal_app.command("list")
def auto_proposal_list(goal_id: str) -> None:
    value = execute(lambda: AutoService(core()).list(goal_id))
    output(value, True)


@auto_app.command("next")
def auto_next(goal_id: str) -> None:
    value = execute(lambda: AutoService(core()).next(goal_id))
    output(value, True)


@auto_app.command("loop")
def auto_loop(
    goal_id: str,
    max_experiments: Annotated[int, typer.Option("--max-experiments")] = 5,
    max_runs: Annotated[int, typer.Option("--max-runs")] = 20,
    time_budget: Annotated[str, typer.Option("--time-budget")] = "2h",
) -> None:
    value = execute(
        lambda: AutoService(core()).loop(
            goal_id,
            max_experiments=max_experiments,
            max_runs=max_runs,
            time_budget=parse_duration(time_budget),
        )
    )
    output(value, True)


@report_app.command("generate")
def report_generate(goal_id: str) -> None:
    def operation() -> list[str]:
        reports = core().reports
        return [
            str(reports.export_markdown(goal_id)),
            str(reports.export_json(goal_id)),
            str(reports.export_csv(goal_id)),
        ]

    for path in execute(operation):
        console.print(path)


@app.command("tui")
def tui_command() -> None:
    execute(lambda: run_tui(core()))


def main() -> None:
    app()
