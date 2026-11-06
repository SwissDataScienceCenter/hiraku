from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig

FAKE_SBATCH = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_LOG/sbatch.args"
env > "$FAKE_LOG/sbatch.env"
if [ -n "${FAKE_SBATCH_ERROR:-}" ]; then
  echo "sbatch: error: $FAKE_SBATCH_ERROR" >&2
  exit 1
fi
case " $* " in
  *" --parsable "*) echo "4242;alps" ;;
  *) echo "Submitted batch job 4242"; exit "${FAKE_JOB_EXIT:-0}" ;;
esac
"""

FAKE_SRUN = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_LOG/srun.args"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --*) shift ;;
    *) break ;;
  esac
done
exec "$@"
"""


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _project(path: Path) -> HirakuConfig:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    config = HirakuConfig.init(path)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return config


def _fake_slurm(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("sbatch", FAKE_SBATCH), ("srun", FAKE_SRUN)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    log = tmp_path / "log"
    log.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SLURM_", "SBATCH_", "HIRAKU_"))
    }
    environment.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    environment.update(FAKE_LOG=str(log))
    return environment


def _dispatch(
    config: HirakuConfig, name: str, *command: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    environment = {
        "HIRAKU_ROOT": str(config.root),
        "HIRAKU_RECORD_ID": "exp-1",
        "HIRAKU_COMMIT": "a" * 40,
        **env,
    }
    return subprocess.run(
        [str(config.dispatch_dir / name), "--", *command],
        cwd=config.root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_init_installs_executable_slurm_dispatchers(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    for name in ("slurm.sh", "srun.sh"):
        assert (config.dispatch_dir / name).stat().st_mode & 0o111
    assert (config.dispatch_dir / "slurm.sbatch").is_file()


def test_slurm_submits_job_script_with_command(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    result = _dispatch(
        config, "slurm.sh", "python", "train.py", "--lr", "1e-3", env=env
    )

    assert result.returncode == 0, result.stderr
    log_dir = config.root / "logs" / "slurm"
    assert result.stdout.splitlines() == [
        "slurm_job_id: 4242",
        f"slurm_log: {log_dir}/exp-1-4242.out",
    ]
    assert log_dir.is_dir()
    args = _lines(tmp_path / "log" / "sbatch.args")
    assert args[:4] == [
        "--parsable",
        f"--chdir={config.root}",
        "--job-name=exp-1",
        f"--output={log_dir}/%x-%j.out",
    ]
    assert args[4:] == [
        str(config.dispatch_dir / "slurm.sbatch"),
        "python",
        "train.py",
        "--lr",
        "1e-3",
    ]
    submitted_env = _lines(tmp_path / "log" / "sbatch.env")
    assert f"HIRAKU_DISPATCH_DIR={config.dispatch_dir}" in submitted_env
    assert "HIRAKU_RECORD_ID=exp-1" in submitted_env
    (log_dir / "exp-1-4242.out").write_text("log\n", encoding="utf-8")
    assert _git(config.root, "status", "--porcelain") == ""


def test_slurm_passes_extra_sbatch_options_after_defaults(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    (config.root / "--constraint=gpu1").touch()
    env.update(HIRAKU_SBATCH_ARGS=" --nodes=4  --ntasks-per-node=4 --constraint=gpu* ")
    result = _dispatch(config, "slurm.sh", "true", env=env)

    assert result.returncode == 0, result.stderr
    args = _lines(tmp_path / "log" / "sbatch.args")
    assert args[2].startswith("--job-name=")
    assert args[3].startswith("--output=")
    assert args[4:] == [
        "--nodes=4",
        "--ntasks-per-node=4",
        "--constraint=gpu*",
        str(config.dispatch_dir / "slurm.sbatch"),
        "true",
    ]


def test_slurm_leaves_name_and_output_to_sbatch_variables(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(SBATCH_JOB_NAME="custom", SBATCH_OUTPUT="/tmp/x-%j.out")
    result = _dispatch(config, "slurm.sh", "true", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["slurm_job_id: 4242"]
    args = _lines(tmp_path / "log" / "sbatch.args")
    assert not any(arg.startswith(("--job-name", "--output")) for arg in args)
    assert not (config.root / "logs").exists()


def test_slurm_wait_returns_job_exit_code(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(SBATCH_WAIT="1", FAKE_JOB_EXIT="3")
    result = _dispatch(config, "slurm.sh", "false", env=env)

    assert result.returncode == 3
    assert result.stdout.strip() == "Submitted batch job 4242"
    assert "--parsable" not in _lines(tmp_path / "log" / "sbatch.args")


def test_slurm_reports_sbatch_errors(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(FAKE_SBATCH_ERROR="Invalid account or account/partition combination")
    result = _dispatch(config, "slurm.sh", "true", env=env)

    assert result.returncode == 1
    assert "Invalid account" in result.stderr
    assert "slurm_job_id" not in result.stdout


def test_slurm_needs_job_script(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    (config.dispatch_dir / "slurm.sbatch").unlink()
    result = _dispatch(config, "slurm.sh", "true", env=env)

    assert result.returncode == 2
    assert "job script not found" in result.stderr


def test_slurm_drops_variables_of_enclosing_allocation(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(SLURM_JOB_ID="99", SLURM_NTASKS="8", SLURM_CONF="/etc/slurm.conf")
    result = _dispatch(config, "slurm.sh", "true", env=env)

    assert result.returncode == 0, result.stderr
    submitted_env = _lines(tmp_path / "log" / "sbatch.env")
    assert not any(
        line.startswith(("SLURM_JOB_ID=", "SLURM_NTASKS=")) for line in submitted_env
    )
    assert "SLURM_CONF=/etc/slurm.conf" in submitted_env


@pytest.mark.parametrize(
    ("arguments", "message"),
    [((), "missing command after --"), (("--foo",), "unknown option: --foo")],
)
def test_slurm_rejects_invalid_arguments(
    tmp_path: Path, arguments: tuple[str, ...], message: str
) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    result = subprocess.run(
        [str(config.dispatch_dir / "slurm.sh"), *arguments],
        cwd=config.root,
        env={**env, "HIRAKU_ROOT": str(config.root)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert message in result.stderr


def test_slurm_needs_sbatch(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _dispatch(config, "slurm.sh", "true", env={"PATH": str(empty)})
    assert result.returncode == 127
    assert "sbatch not found" in result.stderr


def test_job_script_runs_command_through_launch(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    head = _git(config.root, "rev-parse", "HEAD")
    env = _fake_slurm(tmp_path)
    env.update(
        HIRAKU_ROOT=str(config.root),
        HIRAKU_DISPATCH_DIR=str(config.dispatch_dir),
        HIRAKU_RECORD_ID="exp-1",
        SLURM_JOB_ID="4242",
    )
    result = subprocess.run(
        [
            "bash",
            str(config.dispatch_dir / "slurm.sbatch"),
            "/bin/sh",
            "-c",
            'printf "%s|%s|%s" "$PWD" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1].endswith(f"|{head}|clean")
    assert result.stdout.splitlines()[-1].startswith(str(config.root.resolve()))


def test_srun_runs_command_through_launch_and_returns_its_code(
    tmp_path: Path,
) -> None:
    config = _project(tmp_path / "project")
    head = _git(config.root, "rev-parse", "HEAD")
    env = _fake_slurm(tmp_path)
    result = _dispatch(
        config,
        "srun.sh",
        "/bin/sh",
        "-c",
        'printf "%s" "$HIRAKU_COMMIT"; exit 5',
        env=env,
    )

    assert result.returncode == 5
    assert result.stdout == head
    args = _lines(tmp_path / "log" / "srun.args")
    assert args[:3] == [
        "--job-name=exp-1",
        str(config.dispatch_dir / "launch.sh"),
        "--",
    ]
    assert "--pty" not in args


def test_srun_passes_extra_options_and_keeps_job_name_variable(
    tmp_path: Path,
) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(SLURM_JOB_NAME="debug", HIRAKU_SRUN_ARGS="--nodes=2 --gpus-per-node=4")
    result = _dispatch(config, "srun.sh", "true", env=env)

    assert result.returncode == 0, result.stderr
    assert _lines(tmp_path / "log" / "srun.args") == [
        "--nodes=2",
        "--gpus-per-node=4",
        str(config.dispatch_dir / "launch.sh"),
        "--",
        "true",
    ]


def test_srun_shares_an_existing_allocation(tmp_path: Path) -> None:
    config = _project(tmp_path / "project")
    env = _fake_slurm(tmp_path)
    env.update(SLURM_JOB_ID="888")
    result = _dispatch(config, "srun.sh", "true", env=env)

    assert result.returncode == 0, result.stderr
    args = _lines(tmp_path / "log" / "srun.args")
    assert args[:2] == ["--overlap", "--ntasks=1"]
    assert not any(arg.startswith("--job-name") for arg in args)


def test_run_submits_through_slurm_dispatcher(tmp_path: Path) -> None:
    from hiraku.execution import execute_command
    from hiraku.record import Record

    config = _project(tmp_path / "project")
    Record.create("exp-1", config=config)
    env = _fake_slurm(tmp_path)
    result = execute_command(
        ["uv", "run", "python", "train.py"],
        record_id="exp-1",
        dispatch="slurm",
        env={"PATH": env["PATH"], "FAKE_LOG": env["FAKE_LOG"]},
        config=config,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "slurm_job_id: 4242"
    submitted_env = _lines(tmp_path / "log" / "sbatch.env")
    assert "HIRAKU_RECORD_ID=exp-1" in submitted_env
    assert f"HIRAKU_ROOT={config.root}" in submitted_env


@pytest.mark.skipif(
    os.environ.get("HIRAKU_TEST_SLURM") != "1" or shutil.which("sbatch") is None,
    reason="real Slurm test: set HIRAKU_TEST_SLURM=1 on a Slurm login node",
)
def test_real_slurm_batch_job_runs_command(tmp_path: Path) -> None:
    from hiraku.execution import execute_command
    from hiraku.record import Record

    root = Path(os.environ.get("HIRAKU_TEST_SLURM_DIR", tmp_path)) / "project"
    root.mkdir(parents=True)
    config = _project(root)
    Record.create("slurm-smoke", config=config)
    result = execute_command(
        ["/bin/sh", "-c", 'printf "%s" "$HIRAKU_RECORD_ID" > result.txt'],
        record_id="slurm-smoke",
        dispatch="slurm",
        env={"SBATCH_WAIT": "1", "SBATCH_TIMELIMIT": "00:02:00"},
        config=config,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "result.txt").read_text() == "slurm-smoke"
