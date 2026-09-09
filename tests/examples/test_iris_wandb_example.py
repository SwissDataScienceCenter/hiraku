from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.data import load_experiment, load_task

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "iris-wandb"

FAKE_WANDB = r"""
import json
import os
from pathlib import Path

STORE = Path(os.environ["FAKE_WANDB_STORE"])


class _Run:
    def __init__(self, options):
        self.id = "fake-run-1"
        self.config = options["config"]
        self.summary = {}
        self.tags = options.get("tags", [])
        self.created_at = "2026-01-01T12:00:00Z"
        self.url = "https://wandb.test/run/fake-run-1"
        self.state = "finished"
        self.entity = options.get("entity")
        self.project = options["project"]
        self.notes = options.get("notes")
        self.metadata = {"python": "test"}


class _Handle:
    def __init__(self, options):
        self.options = options
        self.summary = {}

    def log(self, metrics):
        self.summary.update(metrics)

    def finish(self):
        record = dict(self.options)
        record["summary"] = self.summary
        STORE.write_text(json.dumps([record]), encoding="utf-8")


_active = None


def init(**options):
    global _active
    _active = _Handle(options)
    return _active


def log(metrics):
    _active.log(metrics)


class Api:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def runs(self, path):
        if not STORE.exists():
            return []
        result = []
        for options in json.loads(STORE.read_text(encoding="utf-8")):
            run = _Run(options)
            run.summary = options.get("summary", {})
            result.append(run)
        return result
"""


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


def test_documented_iris_wandb_workflow(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "iris-wandb"
    shutil.copytree(EXAMPLE, project)
    (project / "wandb.py").write_text(FAKE_WANDB, encoding="utf-8")
    store = tmp_path / "wandb-runs.json"
    monkeypatch.setenv("FAKE_WANDB_STORE", str(store))
    monkeypatch.chdir(project)
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.delitem(sys.modules, "wandb", raising=False)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    invoke(runner, ["init"])
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Iris W&B example")
    source_commit = git(project, "rev-parse", "HEAD")

    invoke(
        runner,
        [
            "task",
            "create",
            "iris",
            "--goal",
            "Maximize Iris test accuracy",
            "--question",
            "How accurately can a classifier predict Iris species?",
            "--set",
            "metrics=[{name: test/accuracy, direction: maximize}, "
            "{name: test/f1_macro, direction: maximize}, "
            "{name: test/log_loss, direction: minimize}]",
            "--set",
            "tracking_backend={type: wandb, project: hiraku-iris}",
        ],
    )
    config = HirakuConfig.discover(project)
    task = load_task("iris", config=config)
    assert task.tracking_backend == {"type": "wandb", "project": "hiraku-iris"}

    invoke(
        runner,
        [
            "experiment",
            "create",
            "logistic",
            "--summary",
            "Logistic regression baseline",
            "--hypothesis",
            "A linear classifier provides a strong Iris baseline.",
            "--commit",
            "HEAD",
            "--set",
            f"entrypoints={{train: {{command: [{sys.executable}, train.py], args: "
            "{model: logistic, seed: 42, test-size: 0.25}}}",
        ],
    )
    experiment = load_experiment("logistic", task=task, config=config)
    assert experiment.commit == source_commit
    assert experiment.entrypoint("train").args == {
        "model": "logistic",
        "seed": 42,
        "test-size": 0.25,
    }

    invoke(runner, ["launch", "logistic:train"])
    rows = json.loads(
        invoke(runner, ["run", "list", "--task", "iris", "--json"]).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "iris"
    assert row["experiment_id"] == "logistic"
    assert row["commit"] in (None, "")
    assert row["entrypoint"] == "train"
    assert row["params"]["model"] == "logistic"
    assert row["params"]["seed"] == 42
    assert row["params"]["test_size"] == 0.25
    assert row["params"]["dataset.name"] == "scikit-learn iris"
    assert row["params"]["dataset.samples"] == 150
    assert row["params"]["dataset.features"] == 4
    assert row["params"]["python.version"]
    assert row["metrics"]["test/accuracy"] == pytest.approx(0.9473684210526316)
    assert row["raw"]["tags"] == ["hiraku", "iris", "logistic"]

    filtered = json.loads(
        invoke(
            runner,
            ["run", "list", "--task", "iris", "--experiment", "logistic", "--json"],
        ).output
    )
    assert filtered == rows
