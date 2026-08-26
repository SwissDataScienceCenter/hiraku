from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from typing import Any

from hiraku.config import load_project
from hiraku.errors import HirakuError
from hiraku.experiments import create_experiment, delete_experiment, set_experiment
from hiraku.records import (
    list_experiments,
    read_experiment,
    read_task,
    resolve_task,
    split_object,
)
from hiraku.tasks import create_task, init_project
from hiraku.util import json_dump, render_table


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hiraku", description="AI-assisted research workflows")
    root.add_argument("--version", action="version", version="hiraku 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize Hiraku in the current Git repository")

    task = commands.add_parser("task", help="Manage tasks")
    task.add_argument("--json", action="store_true")
    task.add_argument("--verbose", action="store_true")
    task_commands = task.add_subparsers(dest="task_command")
    task_create = task_commands.add_parser("create", help="Create a task")
    task_create.add_argument("task_id")
    task_create.add_argument("--template")
    task_create.add_argument("--branch")
    task_show = task_commands.add_parser("show", help="Show a task")
    task_show.add_argument("task_id")
    task_show.add_argument("--json", action="store_true")
    task_show.add_argument("--verbose", action="store_true")
    task_entrypoints = task_commands.add_parser(
        "entrypoints", help="List a task's named entrypoints"
    )
    task_entrypoints.add_argument("task_id", nargs="?")
    task_entrypoints.add_argument("--json", action="store_true")

    experiment = commands.add_parser("experiment", aliases=["exp"], help="Manage experiments")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    experiment_create = experiment_commands.add_parser("create", help="Create an experiment")
    experiment_create.add_argument("experiment")
    experiment_create.add_argument("--task")
    experiment_create.add_argument("--format", choices=["yaml", "markdown"], default="yaml")
    add_experiment_fields(experiment_create)
    experiment_list = experiment_commands.add_parser("list", help="List experiments")
    experiment_list.add_argument("task_id", nargs="?")
    experiment_list.add_argument("--task")
    experiment_list.add_argument("--json", action="store_true")
    experiment_show = experiment_commands.add_parser("show", help="Show an experiment")
    experiment_show.add_argument("experiment")
    experiment_show.add_argument("--task")
    experiment_show.add_argument("--json", action="store_true")
    experiment_set = experiment_commands.add_parser("set", help="Update an experiment")
    experiment_set.add_argument("experiment")
    experiment_set.add_argument("--task")
    add_experiment_fields(experiment_set)
    experiment_delete = experiment_commands.add_parser("delete", help="Delete an experiment")
    experiment_delete.add_argument("experiment")
    experiment_delete.add_argument("--task")

    # Launcher and runs parsers are attached lazily by their modules.
    from hiraku.launcher import add_launch_parser
    from hiraku.tracking import add_runs_parser

    add_launch_parser(commands)
    add_runs_parser(commands)
    return root


def add_experiment_fields(command: argparse.ArgumentParser) -> None:
    command.add_argument("--summary")
    command.add_argument("--hypothesis")
    command.add_argument("--status", choices=["proposed", "active", "evaluated", "abandoned", ""])
    command.add_argument(
        "--decision", choices=["pending", "accepted", "rejected", "inconclusive", ""]
    )
    command.add_argument("--commit")
    command.add_argument("--entrypoint", dest="entrypoints", action="append")
    command.add_argument("--conclusion")
    command.add_argument("--description")


def experiment_updates(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: getattr(args, key)
        for key in (
            "summary",
            "hypothesis",
            "status",
            "decision",
            "commit",
            "entrypoints",
            "conclusion",
            "description",
        )
        if hasattr(args, key)
    }


def selected_object(project: Any, value: str, option: str | None) -> tuple[str, str]:
    explicit_task, object_id = split_object(value)
    task_id = resolve_task(project, explicit_task, option)
    return task_id, object_id


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        root, created = init_project()
        print(f"Initialized Hiraku in {root}")
        print(
            f"Created {len(created)} managed file(s)."
            if created
            else "Hiraku was already initialized."
        )
        return 0

    project = load_project()
    if args.command == "task":
        if args.task_command == "create":
            directory, branch, branch_created = create_task(
                project, args.task_id, args.template, args.branch
            )
            print(f"Created task {args.task_id} at {directory}")
            state = "created" if branch_created else "already exists"
            print(f"Task branch {branch!r} {state}. Switch with: git switch {branch}")
            return 0
        if args.task_command == "entrypoints":
            from hiraku.launcher import load_launch

            task_id = resolve_task(project, args.task_id)
            read_task(project, task_id)
            entrypoints = load_launch(project.root, project, task_id)
            rows = [asdict(entrypoint) for entrypoint in entrypoints.values()]
            if args.json:
                print(json_dump(rows))
            else:
                display = [
                    {
                        "name": row["name"],
                        "command": row["command"],
                        "cwd": row["cwd"],
                        "args": row["args"],
                    }
                    for row in rows
                ]
                print(render_table(display, ["name", "command", "cwd", "args"]))
            return 0
        task_id = args.task_id if args.task_command == "show" else resolve_task(project, None)
        record = read_task(project, task_id)
        verbose = args.verbose
        if args.json:
            print(json_dump(record.as_dict(verbose=verbose)))
        else:
            print(f"Task: {record.id}\nBranch: {record.branch}\nGoal: {record.goal}")
            if verbose and record.body.strip():
                print(f"\n{record.body.rstrip()}")
        return 0

    if args.command in {"experiment", "exp"}:
        if args.experiment_command == "list":
            task_id = resolve_task(project, args.task_id, args.task)
            from hiraku.tracking import enrich_experiments

            rows = enrich_experiments(project, task_id, list_experiments(project, task_id))
            if args.json:
                print(json_dump(rows))
            else:
                columns = [
                    "id",
                    "summary",
                    "hypothesis",
                    "status",
                    "decision",
                    "commit",
                    "run_count",
                ]
                print(render_table(rows, columns))
            return 0
        task_id, experiment_id = selected_object(project, args.experiment, args.task)
        if args.experiment_command == "create":
            record = create_experiment(
                project, task_id, experiment_id, args.format, experiment_updates(args)
            )
            print(f"Created experiment {task_id}:{experiment_id} at {record.path}")
        elif args.experiment_command == "show":
            record = read_experiment(project, task_id, experiment_id)
            from hiraku.tracking import enrich_experiments

            value = enrich_experiments(project, task_id, [record])[0]
            print(json_dump(value) if args.json else render_experiment(value, record.body))
        elif args.experiment_command == "set":
            record = set_experiment(project, task_id, experiment_id, experiment_updates(args))
            print(f"Updated experiment {task_id}:{experiment_id} at {record.path}")
        elif args.experiment_command == "delete":
            path = delete_experiment(project, task_id, experiment_id)
            print(f"Deleted experiment {task_id}:{experiment_id} ({path})")
        return 0

    if args.command == "launch":
        from hiraku.launcher import launch

        return launch(project, args)
    if args.command == "runs":
        from hiraku.tracking import show_runs

        return show_runs(project, args)
    raise AssertionError(f"Unhandled command: {args.command}")


def render_experiment(value: dict[str, Any], body: str) -> str:
    fields = [
        "id",
        "task_id",
        "summary",
        "hypothesis",
        "status",
        "decision",
        "commit",
        "entrypoints",
        "conclusion",
        "created_at",
        "updated_at",
        "run_count",
        "git_parents",
        "commit_mismatches",
    ]
    lines = [f"{field}: {value.get(field, '')}" for field in fields]
    if body.strip():
        lines.extend(["", body.rstrip()])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    try:
        code = dispatch(parser().parse_args(argv))
    except HirakuError as exc:
        print(f"hiraku: error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        print("hiraku: interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
