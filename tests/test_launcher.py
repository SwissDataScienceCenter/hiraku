from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from conftest import commit_all, git, run_process


def prepare_experiment(repo: Path, command: list[str]) -> Path:
    run_process([*command, "init"], repo)
    commit_all(repo, "initialize")
    run_process([*command, "exp", "create", "launch-test"], repo)
    script = repo / "capture.py"
    script.write_text(
        """import json
import os
from pathlib import Path

Path("observed.json").write_text(json.dumps({
    "experiment": os.environ["HIRAKU_EXPERIMENT_ID"],
    "commit": os.environ["HIRAKU_GIT_COMMIT"],
    "backend": os.environ["HIRAKU_TRACKING_BACKEND"],
    "table": os.environ["HIRAKU_TABLE_PATH"],
    "custom": os.environ["CUSTOM_VALUE"],
}))
""",
        encoding="utf-8",
    )
    manifest = {
        "entrypoints": {
            "capture": {
                "command": [sys.executable, "capture.py"],
                "cwd": ".",
                "env": {"CUSTOM_VALUE": "manifest"},
            }
        }
    }
    (repo / "experiments/launch-test/launch.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    commit_all(repo, "configure launch")
    return script


def test_launch_injects_reproducibility_environment(repo: Path, hiraku_command: list[str]) -> None:
    prepare_experiment(repo, hiraku_command)
    expected_commit = git(repo, "rev-parse", "HEAD").stdout.strip()

    run_process([*hiraku_command, "launch", "launch-test:capture"], repo)

    observed = json.loads((repo / "observed.json").read_text(encoding="utf-8"))
    assert observed == {
        "experiment": "launch-test",
        "commit": expected_commit,
        "backend": "table",
        "table": str(repo / ".hiraku/runs.csv"),
        "custom": "manifest",
    }


def test_launch_rejects_dirty_or_wrong_branch(repo: Path, hiraku_command: list[str]) -> None:
    prepare_experiment(repo, hiraku_command)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    dirty = run_process([*hiraku_command, "launch", "launch-test:capture"], repo, check=False)
    assert dirty.returncode == 1
    assert "completely clean" in dirty.stderr

    (repo / "dirty.txt").unlink()
    git(repo, "switch", "-c", "alternate")
    wrong = run_process([*hiraku_command, "launch", "launch-test:capture"], repo, check=False)
    assert wrong.returncode == 1
    assert "requires branch" in wrong.stderr


def test_launch_rejects_reserved_environment(repo: Path, hiraku_command: list[str]) -> None:
    prepare_experiment(repo, hiraku_command)
    manifest_path = repo / "experiments/launch-test/launch.yml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["entrypoints"]["capture"]["env"] = {"HIRAKU_GIT_COMMIT": "fake"}
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    commit_all(repo, "invalid manifest")

    result = run_process([*hiraku_command, "launch", "launch-test:capture"], repo, check=False)

    assert result.returncode == 1
    assert "cannot override reserved variable" in result.stderr


def test_launch_propagates_child_exit_code(repo: Path, hiraku_command: list[str]) -> None:
    prepare_experiment(repo, hiraku_command)
    manifest_path = repo / "experiments/launch-test/launch.yml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["entrypoints"]["failure"] = {
        "command": [sys.executable, "-c", "raise SystemExit(7)"],
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    commit_all(repo, "failure entrypoint")

    result = run_process([*hiraku_command, "launch", "launch-test:failure"], repo, check=False)

    assert result.returncode == 7
