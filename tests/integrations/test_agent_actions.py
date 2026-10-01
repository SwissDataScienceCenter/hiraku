"""Opt-in live agent test for the Heart Disease example."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("HIRAKU_RUN_AGENT_ACTION_TESTS") != "1",
        reason="set HIRAKU_RUN_AGENT_ACTION_TESTS=1 for live Codex action tests",
    ),
]


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def _invoke(runner: CliRunner, arguments: list[str]) -> dict:
    result = runner.invoke(main, [*arguments, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_codex_drafts_and_implements_heart_disease_model(
    tmp_path: Path, monkeypatch
) -> None:
    assert shutil.which("codex"), "Codex CLI must be installed and authenticated"
    project = tmp_path / "heart-disease"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Test")
    _git(project, "config", "user.email", "test@example.invalid")
    config = HirakuConfig.init(project, example="heart-disease", ignore_git=False)
    task = Record.create(
        "heart-disease",
        type="task",
        config=config,
        goal="Compare predictive models for heart disease",
    )
    _git(project, "add", ".")
    _git(project, "commit", "-m", "initialize")
    monkeypatch.chdir(project)
    runner = CliRunner()
    drafted = _invoke(
        runner,
        [
            "agent",
            "experiment",
            "draft",
            "--record",
            task.id,
            "--provider",
            "codex",
            "--model",
            "gpt-5.6-luna",
            "--reasoning-effort",
            "low",
            "-m",
            "Draft one experiment with id gradient-boosting to test adding "
            "GradientBoostingClassifier to the Heart Disease train.py example. "
            "Include an implementation brief: use model name gradient_boosting, "
            "update argparse choices and build_model, preserve existing models "
            "and metrics.",
        ],
    )
    assert drafted["record"] == "gradient-boosting"
    experiment = load_record("gradient-boosting", config=config)
    assert experiment.status == "draft"
    assert experiment.kind == "hypothesis"
    assert experiment.markdown.strip()
    experiment.entrypoints = {
        "train": {
            "command": [sys.executable, "train.py"],
            "args": {"model": "gradient_boosting", "seed": 42},
        }
    }
    experiment.save()
    implemented = _invoke(
        runner,
        [
            "agent",
            "experiment",
            "implement",
            "--record",
            experiment.id,
            "--provider",
            "codex",
            "--model",
            "gpt-5.6-luna",
            "--reasoning-effort",
            "low",
        ],
    )
    assert implemented["status"] == "implemented"
    experiment = load_record(experiment.id, config=config)
    assert experiment.commit == _git(project, "rev-parse", "HEAD")
    launched = runner.invoke(main, ["launch", "train", "--record", experiment.id])
    assert launched.exit_code == 0, launched.output
    assert (project / "runs.csv").is_file()
