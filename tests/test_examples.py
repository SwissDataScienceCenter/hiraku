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
    launch = yaml.safe_load(
        (destination / "tasks/heart-disease/launch.yml").read_text(encoding="utf-8")
    )
    assert set(launch["entrypoints"]) == {"logistic", "random-forest"}
