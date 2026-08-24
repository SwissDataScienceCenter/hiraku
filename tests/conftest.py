from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku_auto.config import load_project
from hiraku_auto.scaffold import create_experiment, create_goal, init_project


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def bare_project(tmp_path: Path):
    """An initialised project in a git repository with one commit."""
    root = tmp_path / "proj"
    root.mkdir()
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    init_project(root, name="Test project")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return load_project(root)


@pytest.fixture()
def project(bare_project):
    """A project with one goal and a baseline/child pair."""
    create_goal(
        bare_project,
        "vo-backend",
        primary_metric="ate_rmse_m",
        direction="minimize",
        target=3.0,
        gate_metric="completion_rate",
    )
    create_experiment(
        bare_project,
        "baseline",
        goal="vo-backend",
        kind="baseline",
        status="approved",
        expectation="Establishes the reference.",
    )
    create_experiment(
        bare_project,
        "candidate",
        parent="baseline",
        status="approved",
        expectation="Lowers ate_rmse_m without lowering completion_rate.",
    )
    return bare_project


@pytest.fixture()
def entrypoint(project):
    """A deterministic experiment program that reports metrics the documented way."""
    script = project.experiments_dir / "candidate" / "train.py"
    script.write_text(
        "import json, os\n"
        "seed = int(os.environ.get('HIRAKU_SEED', '1'))\n"
        "json.dump({'ate_rmse_m': 2.0 + seed * 0.001, 'completion_rate': 0.9},\n"
        "          open(os.environ['HIRAKU_METRICS_PATH'], 'w'))\n",
        encoding="utf-8",
    )
    (project.experiments_dir / "candidate" / "launch.yml").write_text(
        'entrypoints:\n  train:\n    command: ["python3", "train.py"]\n    cwd: .\n',
        encoding="utf-8",
    )
    git(project.root, "add", "-A")
    git(project.root, "commit", "-qm", "entrypoint")
    return project
