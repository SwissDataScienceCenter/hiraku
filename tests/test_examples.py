from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from conftest import hiraku, run

PROJECT_ROOT = Path(__file__).parents[1]


def initialize_copy(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)
    run(destination, "git", "init", "-b", "main")
    run(destination, "git", "config", "user.email", "tests@example.com")
    run(destination, "git", "config", "user.name", "Hiraku Tests")
    hiraku(destination, "init")
    run(destination, "git", "add", ".")
    run(destination, "git", "commit", "-m", "initialize example")


def test_smoke_example_executes_complete_loop(tmp_path: Path) -> None:
    destination = tmp_path / "smoke"
    initialize_copy(PROJECT_ROOT / "examples/smoke", destination)
    hiraku(destination, "task", "create", "smoke", "--template", "smoke")
    run(destination, "git", "switch", "hiraku/task/smoke")
    run(destination, "git", "add", ".")
    run(destination, "git", "commit", "-m", "create smoke task")
    hiraku(
        destination,
        "experiment",
        "create",
        "smoke:baseline",
        "--entrypoint",
        "train --score 0.50",
    )
    hiraku(destination, "experiment", "set", "smoke:baseline", "--commit", "HEAD")
    hiraku(
        destination,
        "launch",
        "--experiment",
        "baseline",
        "smoke:train",
        "--score",
        "0.50",
    )
    rows = json.loads(hiraku(destination, "runs", "smoke", "--json").stdout)
    assert len(rows) == 1
    assert rows[0]["score"] == "0.500000"
    assert rows[0]["commit"]
    assert (destination / "example_tracking.py").is_file()


def test_heart_disease_example_is_checksum_pinned_and_structurally_valid(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "heart-disease"
    initialize_copy(PROJECT_ROOT / "examples/heart-disease", destination)
    hiraku(
        destination,
        "task",
        "create",
        "heart-disease",
        "--template",
        "heart-disease",
    )
    task = json.loads(hiraku(destination, "task", "show", "heart-disease", "--json").stdout)
    assert "checksum-pinned" in task["goal"]
    script = (destination / "train.py").read_text(encoding="utf-8")
    assert "a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8" in script
    assert "LogisticRegression" in script
    assert "RandomForestClassifier" in script
    assert "from example_tracking import record_run" in script
    assert (destination / "example_tracking.py").is_file()
    launch = yaml.safe_load(
        (destination / "tasks/heart-disease/launch.yml").read_text(encoding="utf-8")
    )
    assert set(launch["entrypoints"]) == {"logistic", "random-forest"}
    assert all(
        entrypoint["command"][:2] == ["python", "run_example.py"]
        for entrypoint in launch["entrypoints"].values()
    )

    hiraku(
        destination,
        "task",
        "create",
        "heart-disease-auto",
        "--template",
        "heart-disease-auto",
    )
    auto_task = json.loads(
        hiraku(destination, "task", "show", "heart-disease-auto", "--json").stdout
    )
    assert auto_task["metrics"] == [
        {"name": "val/log_loss", "direction": "minimize"},
        {"name": "val/accuracy", "direction": "maximize"},
    ]
    auto_dir = destination / "tasks/heart-disease-auto"
    auto_program = (auto_dir / "program.md").read_text(encoding="utf-8")
    assert "Iteration limit or wall-clock limit:" in auto_program
    assert "existing dependencies only" in auto_program
    assert "local CPU only" in auto_program
    assert "hiraku runs <task> --json" in auto_program
    auto_launch = yaml.safe_load((auto_dir / "launch.yml").read_text(encoding="utf-8"))
    assert set(auto_launch["entrypoints"]) == {"logistic", "random-forest"}


def test_autoresearch_example_is_pinned_and_cpu_mock_runs(tmp_path: Path) -> None:
    destination = tmp_path / "autoresearch"
    initialize_copy(PROJECT_ROOT / "examples/autoresearch", destination)
    provenance = (destination / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "228791fb499afffb54b46200aca536f79142f117" in provenance
    assert (destination / "LICENSE").is_file()
    program = (destination / ".hiraku/templates/autoresearch/program.md").read_text(
        encoding="utf-8"
    )
    assert program.startswith("# autoresearch\n\nThis is an experiment")
    for heading in (
        "## Setup",
        "## Experimentation",
        "## Output format",
        "## Logging results",
        "## The experiment loop",
    ):
        assert heading in program
    assert "LOOP FOREVER:" in program
    assert "**NEVER STOP**" in program
    assert "git revert --no-edit <experiment-commit>" in program
    assert "git reset back to where you started" in program
    assert "hiraku exp create <task>:<experiment-id> --branch" in program
    assert "merge `hiraku/experiment/<task>/<experiment-id>`" in program
    assert "without merging, keeping the experiment branch" in program
    assert "Do not edit the run table" in program
    hiraku(destination, "task", "create", "autoresearch", "--template", "autoresearch")
    copied_program = (destination / "tasks/autoresearch/program.md").read_text(encoding="utf-8")
    assert copied_program == program
    task_text = (destination / "tasks/autoresearch/task.md").read_text(encoding="utf-8")
    assert "## History mode" in task_text
    assert "Selected mode:" in task_text
    run(destination, "git", "switch", "hiraku/task/autoresearch")
    run(destination, "git", "add", ".")
    run(destination, "git", "commit", "-m", "create autoresearch task")
    hiraku(
        destination,
        "exp",
        "create",
        "autoresearch:contract",
        "--entrypoint",
        "cpu-mock --val-bpb 0.9",
    )
    hiraku(destination, "exp", "set", "autoresearch:contract", "--commit", "HEAD")
    hiraku(
        destination,
        "launch",
        "--experiment",
        "contract",
        "autoresearch:cpu-mock",
        "--val-bpb",
        "0.9",
    )
    rows = json.loads(
        hiraku(
            destination,
            "runs",
            "autoresearch",
            "--sort",
            "val_bpb",
            "--ascending",
            "--json",
        ).stdout
    )
    assert rows[0]["val_bpb"] == "0.9"
    assert rows[0]["_rank"] == 1
