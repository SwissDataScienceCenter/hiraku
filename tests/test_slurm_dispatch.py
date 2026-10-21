"""The bundled Slurm dispatcher, run against a fake sbatch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.launch import launch
from hiraku.record import Record

FAKE_SBATCH = """#!/bin/sh
# Run the job script at once and print a job id, like `sbatch --parsable`.
out=/dev/null; job=
for a in "$@"; do
  case "$a" in --output=*) out=${a#--output=};; --*) ;; *) job=$a;; esac
done
out=$(printf '%s' "$out" | sed "s/%j/42/; s/%x/job/")
SLURM_JOB_ID=42 bash "$job" > "$out" 2>&1
echo 42
"""

PROGRAM = """import os
from pathlib import Path
names = ["HIRAKU_RECORD_ID", "HIRAKU_COMMIT", "HIRAKU_GIT_STATE", "HIRAKU_ENTRYPOINT"]
values = [os.environ[name] for name in names] + [os.getcwd()]
(Path(os.environ["HIRAKU_ROOT"]) / "seen.txt").write_text("\\n".join(values))
"""


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_init_installs_an_executable_slurm_dispatcher(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    slurm = config.dispatch_dir / "slurm.sh"
    assert slurm.is_file() and os.access(slurm, os.X_OK)
    assert (config.dispatch_dir / "slurm.header").is_file()


def test_jobs_run_the_requested_commit_in_a_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sbatch").write_text(FAKE_SBATCH)
    (bin_dir / "sbatch").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.org")
    git(root, "config", "user.name", "Test")
    config = HirakuConfig.init(root, install_skill=False)
    (config.dispatch_dir / "slurm.header").write_text("#SBATCH --time=00:05:00\n")
    (root / "train.py").write_text(PROGRAM)
    (root / ".gitignore").write_text("seen.txt\nhiraku/\nhiraku.yml\n")
    git(root, "add", ".gitignore", "train.py")
    git(root, "commit", "-q", "-m", "experiment")
    sha = git(root, "rev-parse", "HEAD")
    Record.create("exp", type="experiment", activate=False, config=config)
    # The checkout moves on before the job runs; the job still runs `sha`.
    (root / "train.py").write_text(PROGRAM + "# later\n")
    git(root, "commit", "-q", "-am", "later")

    result = launch(
        {"command": ["python3", "train.py"]},
        name="train",
        record_id="exp",
        commit=sha,
        dispatch="slurm.sh",
        capture_output=True,
        config=config,
    )

    assert result.stdout.strip() == "42"
    record, commit, state, entrypoint, cwd = (root / "seen.txt").read_text().split("\n")
    assert (record, commit, state, entrypoint) == ("exp", sha, "clean", "train")
    assert "hiraku-worktrees" in cwd and cwd != str(root)
    logs = list((root / ".git" / "hiraku-worktrees" / "logs").glob("*.out"))
    assert logs
