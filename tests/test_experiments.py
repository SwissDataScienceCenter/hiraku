from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import commit_all, git, run_process

from hiraku.config import load_config
from hiraku.errors import HirakuError
from hiraku.experiments import create_experiment


def initialized_repo(repo: Path, command: list[str]) -> None:
    run_process([*command, "init"], repo)
    commit_all(repo, "initialize hiraku")


def test_create_switches_branch_and_exposes_metadata(repo: Path, hiraku_command: list[str]) -> None:
    initialized_repo(repo, hiraku_command)

    run_process([*hiraku_command, "exp", "create", "first-model"], repo)

    assert git(repo, "branch", "--show-current").stdout.strip() == "exp/first-model"
    record = repo / "experiments/first-model/experiment.md"
    assert record.is_file()
    assert "id: first-model" in record.read_text(encoding="utf-8")
    assert (repo / "experiments/first-model/policy.md").read_text() == (
        repo / ".hiraku/policies/guided.md"
    ).read_text()

    shown = run_process([*hiraku_command, "experiment", "show", "first-model", "--json"], repo)
    assert json.loads(shown.stdout) == {
        "id": "first-model",
        "branch": "exp/first-model",
        "mode": "guided",
    }


def test_create_supports_auto_and_custom_branch(repo: Path, hiraku_command: list[str]) -> None:
    initialized_repo(repo, hiraku_command)

    run_process(
        [
            *hiraku_command,
            "exp",
            "create",
            "forest-test",
            "--mode",
            "auto",
            "--branch",
            "research/forest-test",
        ],
        repo,
    )

    assert git(repo, "branch", "--show-current").stdout.strip() == "research/forest-test"
    assert (repo / "experiments/forest-test/policy.md").read_text() == (
        repo / ".hiraku/policies/auto.md"
    ).read_text()


def test_create_rejects_dirty_tree_and_invalid_id(repo: Path, hiraku_command: list[str]) -> None:
    initialized_repo(repo, hiraku_command)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")

    dirty = run_process([*hiraku_command, "exp", "create", "valid-id"], repo, check=False)
    invalid = run_process([*hiraku_command, "exp", "create", "Invalid_ID"], repo, check=False)

    assert dirty.returncode == 1
    assert "completely clean" in dirty.stderr
    assert invalid.returncode == 1
    assert "lowercase slug" in invalid.stderr


def test_create_rolls_back_branch_and_files(
    repo: Path, hiraku_command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    initialized_repo(repo, hiraku_command)
    config = load_config(repo)
    original_write = Path.write_text

    def failing_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == "launch.yml":
            raise OSError("injected write failure")
        return original_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    with pytest.raises(HirakuError, match="injected write failure"):
        create_experiment(config, "rollback-test")

    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert not (repo / "experiments/rollback-test").exists()
    assert git(repo, "branch", "--list", "exp/rollback-test").stdout == ""


def test_list_rejects_record_directory_mismatch(repo: Path, hiraku_command: list[str]) -> None:
    initialized_repo(repo, hiraku_command)
    run_process([*hiraku_command, "exp", "create", "right-id"], repo)
    path = repo / "experiments/right-id/experiment.md"
    path.write_text(path.read_text().replace("id: right-id", "id: wrong-id"), encoding="utf-8")

    result = run_process([*hiraku_command, "exp", "list", "--json"], repo, check=False)

    assert result.returncode == 1
    assert "does not match directory" in result.stderr
