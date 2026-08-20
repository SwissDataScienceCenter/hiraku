from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import commit_all, run_process

from hiraku.config import load_config
from hiraku.errors import HirakuError


def initialize(repo: Path, command: list[str]) -> None:
    run_process([*command, "init"], repo)
    commit_all(repo, "initialize")


def test_config_is_discovered_from_subdirectory(repo: Path, hiraku_command: list[str]) -> None:
    initialize(repo, hiraku_command)
    child = repo / "nested" / "directory"
    child.mkdir(parents=True)

    config = load_config(child)

    assert config.root == repo
    assert config.table_path == repo / ".hiraku/runs.csv"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("experiments_dir", "../outside", "escapes the project root"),
        ("experiment_template", "/tmp/template.md", "must be relative"),
    ],
)
def test_config_rejects_unsafe_paths(
    repo: Path,
    hiraku_command: list[str],
    field: str,
    value: str,
    message: str,
) -> None:
    initialize(repo, hiraku_command)
    config_path = repo / ".hiraku.yml"
    data = yaml.safe_load(config_path.read_text())
    data[field] = value
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(HirakuError, match=message):
        load_config(repo)


def test_config_rejects_unsupported_tracking_backend(repo: Path, hiraku_command: list[str]) -> None:
    initialize(repo, hiraku_command)
    config_path = repo / ".hiraku.yml"
    data = yaml.safe_load(config_path.read_text())
    data["tracking"] = {"backend": "mlflow", "path": "runs.csv"}
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(HirakuError, match="only tracking.backend: table"):
        load_config(repo)
