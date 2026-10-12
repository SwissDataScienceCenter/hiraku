"""Tests for `check-dispatch` and `setup-dispatch`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.config import HirakuConfig
from hiraku_agent.cli import main
from hiraku_agent.defaults import AgentDefaults, save_defaults
from hiraku_agent.dispatch_setup import (
    check_dispatch,
    detect,
    run_setup_dispatch,
)
from hiraku_agent.errors import AgentError


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "user.name", "Test")
    selected = HirakuConfig.init(project)
    (project / "marker.txt").write_text("one")
    _git(project, "add", ".")
    _git(project, "commit", "-qm", "first")
    (project / "marker.txt").write_text("two")
    _git(project, "commit", "-qam", "second")
    return selected


def stub_agent(payload: dict) -> list[str]:
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


# --------------------------------------------------------------------- check-dispatch


def test_worktree_dispatcher_passes(config: HirakuConfig) -> None:
    result = check_dispatch("worktree.sh", config=config)
    assert result.status == "pass", result.checks


def test_launch_dispatcher_is_caught_ignoring_the_commit(config: HirakuConfig) -> None:
    result = check_dispatch("launch", config=config)
    assert result.status == "fail"
    failed = {name: detail for name, ok, detail in result.checks if not ok}
    assert "ignores --commit" in failed["runs the requested commit"]


def test_slurm_dispatcher_passes_with_a_job_id_and_marker(
    config: HirakuConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    sbatch = bin_dir / "sbatch"
    sbatch.write_text(
        '#!/bin/sh\nfor last; do :; done\nbash "$last" >/dev/null 2>&1\necho "4242"\n'
    )
    sbatch.chmod(0o755)
    venv_bin = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{bin_dir}:{venv_bin}:/usr/bin:/bin")
    monkeypatch.chdir(config.root)

    result = check_dispatch("slurm.sh", config=config)

    assert result.job_id == "4242"
    assert result.status == "pass", result.checks
    assert ("records the job (mark-running)", True, "") in result.checks


def test_cli_set_default_only_after_a_pass(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    failed = runner.invoke(main, ["check-dispatch", "launch.sh", "--set-default"])
    assert failed.exit_code != 0
    assert HirakuConfig.discover(config.root).default_dispatch == "launch.sh"

    passed = runner.invoke(main, ["check-dispatch", "worktree.sh", "--set-default"])
    assert passed.exit_code == 0, passed.output
    assert HirakuConfig.discover(config.root).default_dispatch == "worktree.sh"


# --------------------------------------------------------------------- setup-dispatch


def test_detect_finds_sbatch_files_in_default_repos(
    config: HirakuConfig, tmp_path: Path
) -> None:
    code = tmp_path / "code"
    code.mkdir()
    (code / "train.sbatch").write_text(
        "#!/bin/bash\n#SBATCH --account=abc\n#SBATCH --gpus=1\npython train.py\n"
    )
    save_defaults(AgentDefaults(repos=[str(code)]), config)
    found = detect(config)
    assert {"kind": "slurm", "path": str(code / "train.sbatch")} in found["files"]
    assert "#SBATCH --account=abc" in found["excerpts"][str(code / "train.sbatch")]
    assert "slurm.sh" in found["templates"]


def test_setup_writes_the_slurm_template_and_header(config: HirakuConfig) -> None:
    payload = {
        "backend": "slurm",
        "template": "slurm.sh",
        "name": "slurm.sh",
        "sbatch_lines": ["#SBATCH --account=abc", "rm -rf /", "#SBATCH --gpus=1"],
        "script": None,
        "notes": "from train.sbatch",
    }
    result = run_setup_dispatch(command=stub_agent(payload), config=config)

    header = (config.dispatch_dir / "slurm.header").read_text()
    assert "#SBATCH --account=abc" in header and "#SBATCH --gpus=1" in header
    assert "rm -rf" not in header  # only #SBATCH lines are kept
    assert os.access(config.dispatch_dir / "slurm.sh", os.X_OK)
    assert "check-dispatch slurm.sh" in result.data["next"]
    # The project default is untouched.
    assert HirakuConfig.discover(config.root).default_dispatch == "launch.sh"

    changed = {**payload, "sbatch_lines": ["#SBATCH --account=xyz"]}
    with pytest.raises(AgentError, match="--force"):
        run_setup_dispatch(command=stub_agent(changed), config=config)
    run_setup_dispatch(command=stub_agent(changed), force=True, config=config)
    assert "xyz" in (config.dispatch_dir / "slurm.header").read_text()


def test_setup_writes_a_custom_dispatcher_for_a_backend_without_template(
    config: HirakuConfig,
) -> None:
    script = "#!/bin/sh\nset -eu\necho custom\n"
    payload = {
        "backend": "ray",
        "template": None,
        "name": "ray",
        "script": script,
        "notes": None,
    }
    result = run_setup_dispatch(
        "submit to our Ray cluster", command=stub_agent(payload), config=config
    )
    written = config.dispatch_dir / "ray.sh"
    assert result.written == [written]
    assert written.read_text() == script
    assert os.access(written, os.X_OK)

    with pytest.raises(AgentError, match="#!"):
        run_setup_dispatch(
            command=stub_agent({**payload, "name": "bad", "script": "echo hi"}),
            config=config,
        )


def test_setup_dry_run_shows_the_contract_and_detection(config: HirakuConfig) -> None:
    result = run_setup_dispatch("use slurm", dry_run=True, config=config)
    assert "The contract every dispatcher must keep" in result.prompt
    assert "tools_on_path" in result.prompt
    assert "use slurm" in result.prompt
