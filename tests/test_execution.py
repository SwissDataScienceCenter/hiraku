from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ValidationError
from hiraku.execution import execute_command, validate_command
from hiraku.project import Project


def _git(path: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=check,
    )
    return result.stdout.strip()


def _repository(path: Path) -> Project:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    project = Project.create(path)
    (path / "marker.txt").write_text("committed", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return project


def test_run_executes_in_project_and_returns_output_and_exit_code(
    tmp_path: Path,
) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("root/root", type="task", title="root")
    )
    result = execute_command(
        [sys.executable, "-c", "import os; print(os.getcwd()); raise SystemExit(7)"],
        record_id="root/root",
        project=config,
        capture_output=True,
    )
    assert result.returncode == 7
    assert result.stdout.strip() == str(tmp_path)
    assert result.stderr == ""
    assert not (tmp_path / ".jobs").exists()


def test_run_allows_dirty_tree_and_supplies_provenance(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    config = Project.create(tmp_path)
    record = config.vault.create("root/root", type="task", title="root")
    config.set_active_record(record)
    (tmp_path / "uncommitted.txt").write_text("dirty")
    command = [
        sys.executable,
        "-c",
        "import os; print('|'.join(os.environ[k] for k in "
        "('HIRAKU_RECORD_ID','HIRAKU_COMMIT','HIRAKU_GIT_STATE'))) ",
    ]
    result = execute_command(
        command, record_id="root/root", project=config, capture_output=True
    )
    record_id, commit, git_state = result.stdout.strip().split("|")
    assert record_id == record.id
    assert commit == ""
    assert git_state == "unavailable"


def test_run_requires_explicit_command_and_streams_output(
    tmp_path: Path, monkeypatch
) -> None:
    config = Project.create(tmp_path)
    monkeypatch.chdir(tmp_path)
    config.set_active_record(
        config.vault.create("root/root", type="task", title="root")
    )

    seen: list[tuple[str, ...] | None] = []

    def run(command=None, **kwargs):
        seen.append(tuple(command) if command is not None else None)
        return subprocess.CompletedProcess([], 0, stdout="inherited\n", stderr="")

    monkeypatch.setattr("hiraku.cli.execute_command", run)
    cli = CliRunner().invoke(
        main,
        [
            "run",
            "--record",
            "root/root",
            "--",
            sys.executable,
            "-c",
            "print('explicit')",
        ],
    )
    assert cli.exit_code == 0, cli.output
    assert seen == [(sys.executable, "-c", "print('explicit')")]
    missing = CliRunner().invoke(main, ["run", "--record", "root/root"])
    assert missing.exit_code != 0
    assert "command" in missing.output.lower()


def test_run_rejects_reserved_attribution_environment(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("root/root", type="task", title="root")
    )
    with pytest.raises(ValidationError, match="reserved run attribution"):
        execute_command(
            ["echo", "hello"], env={"HIRAKU_RECORD_ID": "other"}, project=config
        )


def test_run_does_not_reserve_legacy_entrypoint_environment(tmp_path: Path) -> None:
    project = Project.create(tmp_path, install_skill=False)
    project.set_active_record(
        project.vault.create("root/root", type="task", title="root")
    )
    result = execute_command(
        [sys.executable, "-c", "import os; print(os.environ['HIRAKU_ENTRYPOINT'])"],
        env={"HIRAKU_ENTRYPOINT": "custom"},
        project=project,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "custom"


def test_cli_propagates_nonzero_exit_code(tmp_path: Path, monkeypatch) -> None:
    config = Project.create(tmp_path)
    config.set_active_record(
        config.vault.create("root/root", type="task", title="root")
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--record",
            "root/root",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(4)",
        ],
    )
    assert result.exit_code == 4


def test_validate_command_accepts_none_and_copies_argv_sequences() -> None:
    assert validate_command(None) is None

    command = ("python", "train.py")
    validated = validate_command(command)

    assert validated == ["python", "train.py"]
    assert validated is not command


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("python train.py", "argv sequence"),
        (b"python", "argv sequence"),
        ({}, "argv sequence"),
        ([], "non-empty string"),
    ],
)
def test_validate_command_rejects_non_argv_values(value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_command(value)


@pytest.mark.parametrize("value", [[""], [1], ["python", None], ["python", "x\x00y"]])
def test_validate_command_rejects_invalid_arguments(value: list[object]) -> None:
    with pytest.raises(ValidationError):
        validate_command(value)


def test_run_uses_dispatcher_in_current_working_tree(tmp_path: Path) -> None:
    project = _repository(tmp_path)
    project.set_active_record(
        project.vault.create("test/test", type="task", title="test")
    )
    project.dispatch_dir.mkdir()
    (project.dispatch_dir / "test.sh").write_text(
        '#!/bin/sh\n[ "$1" = "--" ] && shift\nexec "$@"\n', encoding="utf-8"
    )
    (project.dispatch_dir / "test.sh").chmod(0o755)
    result = execute_command(
        ["/bin/sh", "-c", "cat marker.txt; printf output > result.txt"],
        record_id="test/test",
        dispatch="test",
        project=project,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == "committed"
    assert (tmp_path / "result.txt").read_text() == "output"
