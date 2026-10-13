"""Create and serve a dashboard demo for each documented Hiraku use case."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .test_dashboard import (
        ROOT,
        check_port,
        create_digits_demo,
        demo_directory,
        port_number,
        reopen_demo,
        serve_demo,
    )
else:
    from test_dashboard import (
        ROOT,
        check_port,
        create_digits_demo,
        demo_directory,
        port_number,
        reopen_demo,
        serve_demo,
    )

EXAMPLES = ("minimal", "heart-disease", "digits-mlflow", "iris-wandb", "autoresearch")
MARKER = ".hiraku-use-case-demo"


@dataclass(frozen=True)
class Demo:
    directory: Path
    example: str
    record_id: str
    tracking_label: str
    mlflow_port: int | None = None


def _commit_example(directory: Path, example: str) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Hiraku dashboard demo",
            "-c",
            "user.email=dashboard@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            f"Initialize {example} dashboard demo",
        ],
        cwd=directory,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _command(script: str) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        str(ROOT),
        "--locked",
        "--all-extras",
        "python",
        script,
    ]


def _launch(
    config, record_id: str, name: str, *, env: dict[str, str] | None = None
) -> None:
    from hiraku.launch import launch
    from hiraku.record import load_record

    entrypoint = load_record(record_id, config=config).entrypoint(name, config=config)
    launch(entrypoint, name=name, record_id=record_id, config=config, env=env)


def create_use_case(
    example: str,
    directory: Path | None = None,
    *,
    epochs: int = 8,
    mlflow_port: int = 5000,
    run_iris: bool = False,
    wandb_entity: str | None = None,
) -> Demo:
    """Create a new isolated example project and its documented records."""
    from hiraku.config import HirakuConfig
    from hiraku.record import Record

    if example not in EXAMPLES:
        raise ValueError(f"Unknown example: {example}")
    if (
        example == "iris-wandb"
        and run_iris
        and not (wandb_entity or os.environ.get("WANDB_ENTITY"))
    ):
        raise ValueError("Iris runs need --wandb-entity or WANDB_ENTITY")
    if example == "digits-mlflow":
        project = create_digits_demo(directory, epochs=epochs, mlflow_port=mlflow_port)
        return Demo(
            project, example, "digits", f"http://127.0.0.1:{mlflow_port}", mlflow_port
        )
    project = demo_directory(directory)
    config = HirakuConfig.init(project, example=example, install_skill=False)
    if example == "autoresearch":
        # Keep the upstream Linux dispatcher intact; this demo logs its own metrics.
        shutil.copyfile(
            ROOT / "scripts/autoresearch_cpu_demo.py", project / "cpu_dashboard.py"
        )
        shutil.copyfile(
            ROOT / "src/hiraku/resources/dispatch/launch.sh",
            config.dispatch_dir / "launch.sh",
        )
        (config.dispatch_dir / "launch.sh").chmod(0o755)
        config.default_dispatch = "launch.sh"
        config.save()
    commit = _commit_example(project, example)
    (project / MARKER).write_text(example + "\n", encoding="utf-8")
    csv_label = str(project / "runs.csv")

    if example == "minimal":
        Record.create(
            "my-task",
            type="task",
            config=config,
            activate=False,
            goal="Improve validation accuracy on my dataset",
            metrics=[{"name": "val/accuracy", "direction": "maximize"}],
        )
        Record.create(
            "baseline",
            parent_id="my-task",
            type="experiment",
            commit=commit,
            config=config,
            activate=False,
            hypothesis="The existing representation establishes a reference.",
            entrypoints={
                "evaluate": {"command": _command("evaluate.py"), "args": {"seed": 0}}
            },
        )
        _launch(config, "baseline", "evaluate")
        return Demo(project, example, "my-task", csv_label)

    if example == "heart-disease":
        Record.create(
            "heart-disease",
            type="task",
            config=config,
            activate=False,
            goal="Develop and evaluate predictive models on the Cleveland cohort",
            question="Which model has the best validation loss and accuracy?",
            metrics=[
                {"name": "val/log_loss", "direction": "minimize"},
                {"name": "val/accuracy", "direction": "maximize"},
            ],
        )
        for record_id, model in (
            ("logistic-baseline", "logistic"),
            ("random-forest", "random_forest"),
        ):
            Record.create(
                record_id,
                parent_id="heart-disease",
                type="experiment",
                commit=commit,
                config=config,
                activate=False,
                summary=f"{model.replace('_', ' ')} baseline",
                entrypoints={
                    "train": {
                        "command": _command("train.py"),
                        "args": {
                            "model": model,
                            "seed": 42,
                            "validation-fraction": 0.25,
                        },
                    }
                },
            )
            _launch(config, record_id, "train")
        return Demo(project, example, "heart-disease", csv_label)

    if example == "iris-wandb":
        entity = wandb_entity or os.environ.get("WANDB_ENTITY")
        backend = {"type": "wandb", "project": "hiraku-iris"}
        if entity:
            backend["entity"] = entity
        Record.create(
            "iris",
            type="task",
            config=config,
            activate=False,
            goal="Maximize Iris test accuracy",
            metrics=[
                {"name": "test/accuracy", "direction": "maximize"},
                {"name": "test/f1_macro", "direction": "maximize"},
                {"name": "test/log_loss", "direction": "minimize"},
            ],
            tracking_backend=backend,
        )
        for record_id, model in (
            ("logistic", "logistic"),
            ("random-forest", "random_forest"),
        ):
            Record.create(
                record_id,
                parent_id="iris",
                type="experiment",
                commit=commit,
                config=config,
                activate=False,
                summary=f"{model.replace('_', ' ')} baseline",
                entrypoints={
                    "train": {
                        "command": _command("train.py"),
                        "args": {
                            "model": model,
                            "seed": 42,
                            "test-size": 0.25,
                        },
                    }
                },
            )
            if run_iris:
                _launch(
                    config,
                    record_id,
                    "train",
                    env={"WANDB_ENTITY": entity, "WANDB_PROJECT": "hiraku-iris"},
                )
        label = (
            f"https://wandb.ai/{entity}/hiraku-iris" if entity else "W&B: hiraku-iris"
        )
        return Demo(project, example, "iris", label)

    Record.create(
        "autoresearch",
        type="task",
        config=config,
        activate=False,
        goal="Minimize val_bpb in a fixed training budget",
        program="autoresearch",
        metrics=[
            {"name": "val_bpb", "direction": "minimize"},
            {"name": "peak_vram_mb", "direction": "minimize"},
        ],
        entrypoints={
            "mock": {
                "command": _command("cpu_dashboard.py"),
                "args": {"val-bpb": 1.0, "peak-vram-mb": 1024.0},
            }
        },
    )
    for record_id, loss, memory in (
        ("cpu-test", 1.0, 1024.0),
        ("lower-loss", 0.85, 1536.0),
        ("lower-memory", 0.95, 768.0),
    ):
        Record.create(
            record_id,
            parent_id="autoresearch",
            type="experiment",
            commit=commit,
            config=config,
            activate=False,
            summary="Simulated CPU demo: validation loss and memory tradeoff",
            entrypoints={
                "mock": {
                    "command": _command("cpu_dashboard.py"),
                    "args": {"val-bpb": loss, "peak-vram-mb": memory},
                }
            },
        )
        _launch(config, record_id, "mock")
    print("Portable CPU demo: three simulated runs, with no GPU or model training.")
    print("Full Autoresearch training needs the upstream checkout and a suitable GPU.")
    return Demo(project, example, "autoresearch", csv_label)


def reopen_use_case(directory: Path, *, mlflow_port: int) -> Demo:
    from hiraku.config import HirakuConfig

    directory = directory.expanduser().resolve()
    if (directory / ".hiraku-dashboard-demo").is_file():
        project, record_id, is_mlflow = reopen_demo(directory, mlflow_port=mlflow_port)
        example = "digits-mlflow" if is_mlflow else "csv"
        label = (
            f"http://127.0.0.1:{mlflow_port}"
            if is_mlflow
            else str(project / "runs.csv")
        )
        return Demo(
            project, example, record_id, label, mlflow_port if is_mlflow else None
        )
    marker = directory / MARKER
    if not marker.is_file():
        raise ValueError(f"Not a use-case dashboard demo: {directory}")
    example = marker.read_text(encoding="utf-8").strip()
    if example not in EXAMPLES or example == "digits-mlflow":
        raise ValueError(f"Unrecognized use-case demo: {directory}")
    config = HirakuConfig.load(directory / "hiraku.yml")
    record_id = {
        "minimal": "my-task",
        "heart-disease": "heart-disease",
        "iris-wandb": "iris",
        "autoresearch": "autoresearch",
    }[example]
    from hiraku.record import load_record

    record = load_record(record_id, config=config)
    if example == "iris-wandb":
        backend = record.tracking_backend or {}
        entity = backend.get("entity") or os.environ.get("WANDB_ENTITY")
        label = (
            f"https://wandb.ai/{entity}/hiraku-iris" if entity else "W&B: hiraku-iris"
        )
    else:
        label = str(directory / "runs.csv")
    return Demo(directory, example, record_id, label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--example", choices=EXAMPLES)
    group.add_argument("--reopen", type=Path, metavar="DEMO_PATH")
    parser.add_argument("--project-dir", type=Path, help="New or empty directory")
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument("--mlflow-port", type=port_number, default=5000)
    parser.add_argument("--epochs", type=int, default=8, help="Digits training epochs")
    parser.add_argument("--wandb-entity", help="W&B account or team for Iris")
    parser.add_argument("--run-iris", action="store_true", help="Log Iris runs to W&B")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-serve", action="store_true")
    args = parser.parse_args(argv)
    if args.reopen and args.project_dir:
        parser.error("--reopen cannot be combined with --project-dir")
    if args.epochs < 1:
        parser.error("--epochs must be positive")
    if args.run_iris and args.example != "iris-wandb":
        parser.error("--run-iris requires --example iris-wandb")
    if args.run_iris and not (args.wandb_entity or os.environ.get("WANDB_ENTITY")):
        parser.error("--run-iris needs --wandb-entity or WANDB_ENTITY")
    try:
        if not args.no_serve:
            check_port(args.port)
            if (
                args.example == "digits-mlflow" or args.reopen
            ) and args.port == args.mlflow_port:
                raise ValueError("Dashboard and MLflow ports must differ")
        demo = (
            reopen_use_case(args.reopen, mlflow_port=args.mlflow_port)
            if args.reopen
            else create_use_case(
                args.example,
                args.project_dir,
                epochs=args.epochs,
                mlflow_port=args.mlflow_port,
                run_iris=args.run_iris,
                wandb_entity=args.wandb_entity,
            )
        )
        print(f"Demo project: {demo.directory}", flush=True)
        if args.no_serve:
            return 0
        launch_environment = None
        if demo.example == "iris-wandb":
            from hiraku.config import HirakuConfig
            from hiraku.record import load_record

            config = HirakuConfig.load(demo.directory / "hiraku.yml")
            backend = load_record("iris", config=config).tracking_backend or {}
            launch_environment = {"WANDB_PROJECT": "hiraku-iris"}
            if backend.get("entity"):
                launch_environment["WANDB_ENTITY"] = backend["entity"]
        return serve_demo(
            demo.directory,
            record_id=demo.record_id,
            port=args.port,
            mlflow_port=demo.mlflow_port,
            no_browser=args.no_browser,
            interactive=True,
            scope="subtree",
            tracking_label=demo.tracking_label,
            launch_environment=launch_environment,
        )
    except KeyboardInterrupt:
        print("\nStopped. Demo files are retained for inspection.")
        return 130
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.")
        return exc.returncode if exc.returncode > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
