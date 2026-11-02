from __future__ import annotations

import ast
import json
import runpy
import shutil
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "iris-wandb"

FAKE_WANDB = r"""
import json
import os
from pathlib import Path

STORE = Path(os.environ["FAKE_WANDB_STORE"])


class _Run:
    def __init__(self, options):
        self.id = options["id"]
        self.config = options["config"]
        self.summary = {}
        self.tags = options.get("tags", [])
        self.created_at = "2026-01-01T12:00:00Z"
        self.url = "https://wandb.test/run/fake-run-1"
        self.state = "finished"
        self.entity = options.get("entity")
        self.project = options.get("project") or os.environ.get("WANDB_PROJECT")
        self.notes = options.get("notes")
        self.metadata = {"python": "test"}


class _Handle:
    def __init__(self, options):
        self.options = options
        self.options["project"] = options.get("project") or os.environ.get(
            "WANDB_PROJECT"
        )
        self.summary = {}
        self.classifier_plot = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish()

    def log(self, metrics):
        self.summary.update(metrics)

    def finish(self):
        record = dict(self.options)
        record["summary"] = self.summary
        record["classifier_plot"] = self.classifier_plot
        STORE.write_text(json.dumps([record]), encoding="utf-8")


_active = None


class _Sklearn:
    def plot_classifier(self, *args, **kwargs):
        _active.classifier_plot = kwargs.get("model_name")


sklearn = _Sklearn()


def init(**options):
    global _active
    options["id"] = os.environ.get("WANDB_RUN_ID", "fake-run-1")
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


def test_iris_attribution_without_launch(monkeypatch):
    monkeypatch.setitem(sys.modules, "wandb", types.ModuleType("wandb"))
    monkeypatch.delenv("HIRAKU_RECORD_ID", raising=False)
    script_path = (
        Path(__file__).parents[2]
        / "src/hiraku/resources/examples/iris-wandb/src/train.py"
    )
    script = runpy.run_path(str(script_path))
    assert script["hiraku_config"]() == {}
    monkeypatch.setenv("HIRAKU_RECORD_ID", "baseline")
    monkeypatch.setenv("HIRAKU_GIT_STATE", "unavailable")
    assert script["hiraku_config"]() == {
        "hiraku.record_id": "baseline",
    }


def test_documented_iris_wandb_workflow(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "iris-wandb"
    shutil.copytree(EXAMPLE, project)
    (project / "wandb.py").write_text(FAKE_WANDB, encoding="utf-8")
    store = tmp_path / "wandb-runs.json"
    monkeypatch.setenv("FAKE_WANDB_STORE", str(store))
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    monkeypatch.chdir(project)
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.delitem(sys.modules, "wandb", raising=False)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    invoke(runner, ["example", "create", "iris-wandb", "."])
    project_settings = tomllib.loads((project / "pyproject.toml").read_text())
    assert "hiraku" not in project_settings["project"]["dependencies"]
    script = ast.parse((project / "train.py").read_text())
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and (node.module == "hiraku" or node.module.startswith("hiraku."))
        for node in ast.walk(script)
    )
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Iris W&B example")
    source_commit = git(project, "rev-parse", "HEAD")

    invoke(
        runner,
        [
            "record",
            "create",
            "iris",
            "--set",
            "goal=Maximize Iris test accuracy",
            "--set",
            "question=How accurately can a classifier predict Iris species?",
            "--set",
            "metrics=[{name: test/accuracy, direction: maximize}, "
            "{name: test/f1_macro, direction: maximize}, "
            "{name: test/log_loss, direction: minimize}]",
        ],
    )
    config = HirakuConfig.discover(project)
    root = load_record("iris", config=config)
    assert config.tracking_backend == "wandb"
    assert "tracking_backend" not in root.to_dict()

    invoke(
        runner,
        [
            "record",
            "create",
            "logistic",
            "--parent",
            "iris",
            "--set",
            "summary=Logistic regression baseline",
            "--set",
            "hypothesis=A linear classifier provides a strong Iris baseline.",
            "--commit",
            "HEAD",
        ],
    )
    record = load_record("logistic", config=config)
    assert record.commit == source_commit
    invoke(
        runner,
        [
            "run",
            "--record",
            "logistic",
            "--",
            sys.executable,
            "train.py",
            "--model",
            "logistic",
            "--seed",
            "42",
            "--test-size",
            "0.25",
        ],
    )
    rows = json.loads(
        invoke(runner, ["runs", "--record", "iris", "--all", "--json"]).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == "logistic"
    assert row["commit"] == source_commit
    run_id = row["id"]
    assert row["params"]["model"] == "logistic"
    assert row["params"]["seed"] == 42
    assert row["params"]["test_size"] == 0.25
    assert set(row["params"]) == {"model", "seed", "test_size"}
    assert row["metrics"]["test/accuracy"] == pytest.approx(0.9473684210526316)
    assert set(row["metrics"]) == {"test/accuracy", "test/f1_macro", "test/log_loss"}
    stored = json.loads(store.read_text(encoding="utf-8"))[0]
    assert stored["id"] == run_id
    assert stored["project"] == "iris-wandb"
    assert stored["config"]["hiraku.record_id"] == "logistic"
    assert stored["classifier_plot"] == "logistic"
    assert "hiraku.commit" not in stored["config"]

    filtered = json.loads(
        invoke(
            runner,
            ["runs", "--record", "logistic", "--json"],
        ).output
    )
    assert filtered == rows
