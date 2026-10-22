from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.dashboard.data import artifact_rows, load
from hiraku.record import list_records


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]) -> str:
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result.output


def reviewed_apply(runner: CliRunner, arguments: list[str]) -> str:
    """Preview ``arguments``, then apply them with the previewed hash."""
    preview = invoke(runner, arguments)
    reviewed = preview.split("content_sha256: ", 1)[1].split()[0]
    return invoke(runner, [*arguments, "--apply", "--reviewed-hash", reviewed])


def availability(config: HirakuConfig) -> dict[str, str]:
    return {
        row["path"]: row["availability"]
        for row in artifact_rows(load(config))
        if row["path"] is not None
    }


def test_datasets_registry_example_walkthrough(tmp_path, monkeypatch) -> None:
    project = tmp_path / "datasets-registry"
    project.mkdir()
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()
    invoke(runner, ["init", "--example", "datasets-registry"])
    git(project, "add", "-A")
    git(project, "commit", "-m", "initialize datasets-registry example")
    config = HirakuConfig.discover(project)
    scan = ["registry", "scan", "--root", "data"]
    remote = ["registry", "import", "remote-registry.yaml"]

    assert "Create:  1" in invoke(runner, scan)
    assert list_records(config=config) == []
    reviewed_apply(runner, scan)
    reviewed_apply(runner, remote)
    assert availability(config) == {
        "data/measurements.csv": "present",
        "archive/scan.tiff": "unknown",
    }

    git(project, "add", "-A")
    git(project, "commit", "-m", "register datasets")
    assert "Unchanged: 1" in reviewed_apply(runner, remote)
    assert "Unchanged: 1" in reviewed_apply(runner, scan)
    assert git(project, "status", "--short") == ""

    with (project / "data" / "measurements.csv").open("a") as handle:
        handle.write("P3,S4,scRNA,pass_1\n")
    assert "Update:  1" in reviewed_apply(runner, scan)

    reviewed_apply(runner, ["registry", "import", "partial-refresh.yaml"])
    assert availability(config)["data/measurements.csv"] == "present"
    assert availability(config)["data/notes.txt"] == "present"

    assert "Update:  1" in reviewed_apply(runner, scan)
    assert availability(config)["data/notes.txt"] == "stale"
    assert availability(config)["data/measurements.csv"] == "present"
    assert not (project / "runs.csv").exists()
