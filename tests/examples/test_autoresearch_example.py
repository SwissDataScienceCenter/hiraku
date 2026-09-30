from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record

REPOSITORY = Path(__file__).parents[2]
DOCS_EXAMPLE = REPOSITORY / "docs" / "examples" / "autoresearch"
EXAMPLE = REPOSITORY / "src" / "hiraku" / "resources" / "examples" / "autoresearch"
OVERLAY = EXAMPLE / "src"
PROGRAM = OVERLAY / "hiraku" / "programs" / "autoresearch" / "program.md"
DISPATCHER = OVERLAY / "hiraku" / "dispatch" / "autoresearch.sh"
LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux",
    reason="the autoresearch dispatcher requires Linux utilities and /proc",
)
UPSTREAM_FILES = {
    "README.md": "# upstream autoresearch\n",
    "prepare.py": "# upstream preparation\n",
    "train.py": "# upstream training\n",
    "program.md": "# upstream program\n",
    ".gitignore": "results.tsv\n",
}


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def invoke(runner: CliRunner, arguments: list[str]):
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    return result


def create_overlay_project(tmp_path: Path, monkeypatch) -> tuple[Path, CliRunner]:
    project = tmp_path / "autoresearch"
    project.mkdir()
    for relative, content in UPSTREAM_FILES.items():
        (project / relative).write_text(content, encoding="utf-8")
    git(project, "init", "-b", "master")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    git(project, "add", ".")
    git(project, "commit", "-m", "upstream")
    gitignore = project / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8")
        + "\n# Hiraku local files and run artifacts\n"
        + "/.agents/\n/.claude/\n/hiraku/*\n/hiraku.yml\n/task.md\n"
        + "/runs.csv\n/run.log\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    invoke(runner, ["init", "--example", "autoresearch"])
    git(project, "add", ".gitignore", "cpu_mock.py")
    git(project, "commit", "-m", "add Hiraku autoresearch workflow")

    git(project, "switch", "-c", "autoresearch/sep10")
    invoke(
        runner,
        [
            "record",
            "create",
            "sep10",
            "--type",
            "task",
            "--set",
            "branch=autoresearch/sep10",
            "--set",
            "goal=Find the best architecture, optimizer, and hyperparameters to "
            "minimize `val_bpb` within the fixed 5-minute training budget.",
            "--markdown-file",
            "task.md",
            "--set",
            "program=autoresearch",
            "--set",
            "metrics=[{name: val_bpb, direction: minimize}]",
            "--set",
            f"entrypoints={{train: {{command: [uv, run, python, train.py]}}, "
            f"mock: {{command: [{sys.executable}, cpu_mock.py], "
            "args: {val-bpb: 1.0}}}",
        ],
    )
    return project, runner


def test_autoresearch_is_an_overlay() -> None:
    expected = {
        "README.md",
        "example.yml",
        "src/hiraku.yml",
        "src/task.md",
        "src/cpu_mock.py",
        "src/hiraku/dispatch/autoresearch.sh",
        "src/hiraku/programs/autoresearch/program.md",
    }
    actual = {
        str(path.relative_to(EXAMPLE))
        for path in EXAMPLE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert actual == expected
    readme = (DOCS_EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert "git clone https://github.com/karpathy/autoresearch.git" in readme
    assert "git checkout" not in readme
    assert "hiraku init --example autoresearch" in readme
    assert "example_dir=" not in readme
    assert "/hiraku/*" in readme
    assert "/.agents/" in readme
    assert "/.claude/" in readme
    assert "--markdown-file task.md" in readme
    assert "hiraku record create sep10" in readme
    assert "branch=autoresearch/sep10" in readme
    assert "hiraku record show sep10 --json" in readme
    assert "hiraku record update cpu-test" in readme
    assert "--set decision=discard" in readme

    task_template = (OVERLAY / "task.md").read_text(encoding="utf-8")
    assert "fixed\ntime budget of 5 minutes" in task_template
    assert "The goal is simple: get the lowest val_bpb." in task_template
    assert "Modify `train.py`" in task_template
    assert "Simplicity criterion" in task_template
    assert "hiraku launch" not in task_template
    assert "git reset" not in task_template
    assert "run.log" not in task_template
    assert "Changelog" not in task_template

    program = PROGRAM.read_text(encoding="utf-8")
    assert "hiraku status --json" in program
    assert "hiraku record tree" in program
    assert "hiraku runs --record <id> --json" in program
    assert "--commit HEAD" in program
    assert "Never rebind a completed attempt" in program
    assert "results.tsv" not in program
    assert "grep " not in program
    assert "Choose the history mode" not in program
    assert "hiraku experiment" not in program
    assert "Changelog" not in program


def test_autoresearch_overlay_initializes_record(tmp_path, monkeypatch):
    project, _ = create_overlay_project(tmp_path, monkeypatch)
    task = load_record("sep10", config=HirakuConfig.discover(project))

    assert (project / "README.md").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "README.md"
    ]
    assert (project / "prepare.py").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "prepare.py"
    ]
    assert (project / "train.py").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "train.py"
    ]
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.startswith(UPSTREAM_FILES[".gitignore"])
    assert "/hiraku/*" in gitignore
    assert "/.agents/" in gitignore
    assert "/.claude/" in gitignore
    assert "/runs.csv" in gitignore
    assert (project / "program.md").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "program.md"
    ]
    installed_program = (project / "hiraku/programs/autoresearch/program.md").read_text(
        encoding="utf-8"
    )
    task_template = (OVERLAY / "task.md").read_text(encoding="utf-8")
    assert installed_program == PROGRAM.read_text(encoding="utf-8")
    assert (project / "task.md").read_text(encoding="utf-8") == task_template
    assert task.markdown == task_template
    assert task.path == project / "hiraku/records/sep10/sep10.md"
    assert task.markdown not in installed_program
    assert installed_program not in task.markdown
    assert task.branch == "autoresearch/sep10"
    assert task.goal == (
        "Find the best architecture, optimizer, and hyperparameters to minimize "
        "`val_bpb` within the fixed 5-minute training budget."
    )
    assert task.type == "task"
    assert task.program == "autoresearch"
    assert task.entrypoints["train"].command == ["uv", "run", "python", "train.py"]
    assert task.entrypoints["mock"].command[-1] == "cpu_mock.py"
    config = HirakuConfig.discover(project)
    assert config.default_dispatch == "autoresearch.sh"
    assert config.default_program == "autoresearch"
    assert (project / "hiraku/dispatch/autoresearch.sh").stat().st_mode & 0o111
    assert git(project, "status", "--porcelain") == ""


@LINUX_ONLY
def test_autoresearch_cpu_mock_tracks_dispatch(tmp_path, monkeypatch, capfd):
    project, runner = create_overlay_project(tmp_path, monkeypatch)
    commit = git(project, "rev-parse", "HEAD")
    invoke(
        runner,
        ["record", "create", "cpu-test", "--parent", "sep10", "--commit", "HEAD"],
    )

    invoke(
        runner,
        ["launch", "mock", "--record", "cpu-test", "--", "--val-bpb", "0.9"],
    )

    assert "val_bpb: 0.900000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "runs",
                "--record",
                "cpu-test",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == "cpu-test"
    assert row["commit"] == commit
    assert row["entrypoint"] == "mock"
    assert row["raw"]["git_state"] == "clean"
    assert row["raw"]["status"] == "finished"
    assert float(row["metrics"]["val_bpb"]) == 0.9


@LINUX_ONLY
def test_autoresearch_dispatcher_records_crash(tmp_path, monkeypatch, capfd):
    _, runner = create_overlay_project(tmp_path, monkeypatch)
    invoke(
        runner, ["record", "create", "failure", "--parent", "sep10", "--commit", "HEAD"]
    )

    failed = runner.invoke(
        main, ["launch", "mock", "--record", "failure", "--", "--fail"]
    )

    assert failed.exit_code != 0
    assert "val_bpb: 0.000000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "runs",
                "--record",
                "failure",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 1
    assert rows[0]["raw"]["status"] == "crashed"
    assert float(rows[0]["metrics"]["val_bpb"]) == 0.0


def dispatcher_environment(root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "HIRAKU_ROOT": str(root),
        "HIRAKU_RECORD_ID": "cpu-test",
        "HIRAKU_ENTRYPOINT": "test",
    }


@LINUX_ONLY
def test_autoresearch_dispatcher_records_timeout_and_invalid_output(tmp_path):
    dispatcher = DISPATCHER
    environment = dispatcher_environment(tmp_path)
    environment["HIRAKU_AUTORESEARCH_TIMEOUT_SECONDS"] = "1"
    timed_out = subprocess.run(
        [
            dispatcher,
            "--",
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(2)",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert timed_out.returncode == 124
    assert "started" in timed_out.stdout

    environment.pop("HIRAKU_AUTORESEARCH_TIMEOUT_SECONDS")
    invalid = subprocess.run(
        [dispatcher, "--", sys.executable, "-c", "print('no summary')"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert invalid.returncode != 0
    assert "no summary" in invalid.stdout

    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["status"] for row in rows] == ["timeout", "crashed"]
    assert rows[0]["metric.val_bpb"] == ""
    assert len({row["id"] for row in rows}) == 2


@LINUX_ONLY
def test_autoresearch_dispatcher_rejects_incompatible_csv(tmp_path):
    dispatcher = DISPATCHER
    csv_path = tmp_path / "runs.csv"
    csv_path.write_text("id,metric.other\nexisting,1\n", encoding="utf-8")

    result = subprocess.run(
        [dispatcher, "--", sys.executable, "-c", "print('val_bpb: 1.0')"],
        cwd=tmp_path,
        env=dispatcher_environment(tmp_path),
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "incompatible CSV header" in result.stderr
    assert csv_path.read_text(encoding="utf-8") == "id,metric.other\nexisting,1\n"


def test_autoresearch_mock_is_tracking_independent(tmp_path):
    result = subprocess.run(
        [sys.executable, str(OVERLAY / "cpu_mock.py")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "val_bpb: 1.000000" in result.stdout
    assert not (tmp_path / "runs.csv").exists()
