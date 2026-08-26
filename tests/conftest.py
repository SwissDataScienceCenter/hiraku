from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hiraku.app import build_app
from hiraku.config import load_config
from hiraku.initialize import initialize


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "hiraku@example.test")
    git(root, "config", "user.name", "Hiraku Test")
    (root / "evaluate.py").write_text(
        """import json, os, sys
from pathlib import Path
if '--fail' in sys.argv: raise SystemExit(9)
Path(os.environ['HIRAKU_METRICS_PATH']).write_text(json.dumps({
    'parameters': {'seed': 42},
    'metrics': {'score': 0.75 if os.environ['HIRAKU_EXPERIMENT_KIND'] == 'baseline' else 0.9}
}))
""",
        encoding="utf-8",
    )
    (root / "implement.py").write_text(
        "from pathlib import Path\nPath('auto-implementation.txt').write_text('implemented')\n",
        encoding="utf-8",
    )
    (root / "cli_log.py").write_text(
        """import subprocess, sys
subprocess.run([sys.executable, '-m', 'hiraku', 'run', 'log', '--param', 'seed=7', '--metric', 'score=0.7'], check=True)
""",
        encoding="utf-8",
    )
    (root / "launch.yml").write_text(
        """entrypoints:
  evaluate:
    command: [python, evaluate.py]
    cwd: .
  fail:
    command: [python, evaluate.py, --fail]
    cwd: .
  no-metrics:
    command: [python, -c, "pass"]
    cwd: .
  killed:
    command: [python, -c, "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]
    cwd: .
  cli-log:
    command: [python, cli_log.py]
    cwd: .
auto:
  allowed_entrypoints: [evaluate]
  implement:
    command: [python, implement.py]
    cwd: .
""",
        encoding="utf-8",
    )
    initialize(root)
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    monkeypatch.chdir(root)
    application = build_app(load_config(root))
    application.goals.create("demo", "score", "max", root / "launch.yml")
    return root, application
