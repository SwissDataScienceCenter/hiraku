from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking import Leaderboard

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

    def log(self, metrics):
        self.summary.update(metrics)

    def finish(self):
        record = dict(self.options)
        record["summary"] = self.summary
        STORE.write_text(json.dumps([record]), encoding="utf-8")


_active = None


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
    git(project, "add", ".")
    git(project, "commit", "-m", "initialize Iris W&B example")
    source_commit = git(project, "rev-parse", "HEAD")

    invoke(
        runner,
        [
            "record",
            "create",
            "iris-classification",
            "--title",
            "Iris classification",
            "--type",
            "task",
            "--set",
            "goal=Maximize Iris test accuracy",
            "--set",
            "question=How accurately can a classifier predict Iris species?",
        ],
    )
    config = Project.discover(project)
    assert Leaderboard.load(config).source.model_dump(exclude_defaults=True) == {
        "project": "iris-wandb"
    }

    invoke(
        runner,
        [
            "record",
            "create",
            "iris-classification/logistic-regression",
            "--title",
            "Logistic regression",
            "--type",
            "experiment",
            "--parent",
            "iris-classification",
            "--set",
            "summary=Logistic regression baseline",
            "--set",
            "hypothesis=A linear classifier provides a strong Iris baseline.",
            "--commit",
            "HEAD",
        ],
    )
    record = config.vault.load("iris-classification/logistic-regression")
    assert record.commit == source_commit
    invoke(
        runner,
        [
            "run",
            "--record",
            "iris-classification/logistic-regression",
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
    rows = json.loads(invoke(runner, ["leaderboard", "runs", "--json"]).output)
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == record.id
    assert row["commit"] is None
    run_id = row["id"]
    assert row["params"]["model"] == "logistic"
    assert row["params"]["seed"] == "42"
    assert row["params"]["test_size"] == "0.25"
    assert row["params"]["dataset.name"] == "scikit-learn iris"
    assert row["params"]["dataset.samples"] == "150"
    assert row["params"]["dataset.features"] == "4"
    assert row["params"]["python.version"]
    assert row["metrics"]["test/accuracy"] == pytest.approx(0.9473684210526316)
    assert row["raw"]["tags"] == ["hiraku", "iris", "logistic"]
    stored = json.loads(store.read_text(encoding="utf-8"))[0]
    assert stored["id"] == run_id
    assert stored["project"] == "iris-wandb"
    assert stored["config"]["hiraku.job_id"] == row["job_id"]
    assert stored["config"]["hiraku.record_id"] == record.id
    assert not (tmp_path / ".hiraku/jobs").exists()

    filtered = json.loads(
        invoke(
            runner,
            [
                "leaderboard",
                "runs",
                "--record",
                "iris-classification/logistic-regression",
                "--json",
            ],
        ).output
    )
    assert filtered == rows
