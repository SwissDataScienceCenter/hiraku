from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.workflows.langgraph import start_workflow, workflow_ids

REPOSITORY = Path(__file__).parents[2]
DOCS_EXAMPLE = REPOSITORY / "docs" / "examples" / "autoresearch"
EXAMPLE = REPOSITORY / "src" / "hiraku" / "resources" / "examples" / "autoresearch"
OVERLAY = EXAMPLE / "src"
WORKFLOW = REPOSITORY / "src" / "hiraku" / "resources" / "workflows" / "collaborative"
LOOP = OVERLAY / "hiraku" / "workflows" / "autoresearch" / "workflow.md"
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
    git(project, "config", "commit.gpgsign", "false")
    git(project, "add", ".")
    git(project, "commit", "-m", "upstream")
    gitignore = project / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8")
        + "\n# Hiraku local files and run artifacts\n"
        + "/.agents/\n/.claude/\n/.codex/\n/.pi/\n/.mcp.json\n/hiraku/*\n"
        + "/hiraku.yml\n"
        + "!/hiraku/records/\n"
        + "/task.md\n"
        + "/runs.csv\n/run.log\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    invoke(runner, ["example", "create", "autoresearch", "."])
    git(project, "add", ".gitignore", "cpu_mock.py")
    git(project, "commit", "-m", "add Hiraku autoresearch workflow")

    git(project, "switch", "-c", "autoresearch/sep10")
    invoke(
        runner,
        [
            "record",
            "create",
            "sep10",
            "--title",
            "Language model optimization",
            "--type",
            "task",
            "--set",
            "branch=autoresearch/sep10",
            "--set",
            "goal=Find the best architecture, optimizer, and hyperparameters to "
            "minimize `val_bpb` within the fixed 5-minute training budget.",
            "--markdown-file",
            "task.md",
        ],
    )
    git(project, "add", "hiraku")
    git(project, "commit", "-m", "add autoresearch task record")
    return project, runner


def test_autoresearch_is_an_overlay() -> None:
    expected = {
        "README.md",
        "example.yml",
        "src/hiraku.yml",
        "src/hiraku/leaderboards/default.yml",
        "src/task.md",
        "src/cpu_mock.py",
        "src/hiraku/dispatch/autoresearch.sh",
        "src/hiraku/workflows/autoresearch/workflow.md",
        "src/hiraku/workflows/autoresearch/workflow.py",
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
    assert "hiraku example create autoresearch ." in readme
    assert "example_dir=" not in readme
    assert "/hiraku/*" in readme
    assert "/.agents/" in readme
    assert "/.claude/" in readme
    assert "/.mcp.json" in readme
    assert "/.codex/" in readme
    assert "workflow_start" in readme
    assert "hiraku program" not in readme
    assert "--markdown-file task.md" in readme
    assert "hiraku record create sep10" in readme
    assert "branch=autoresearch/sep10" in readme
    assert "hiraku workflow status" in readme
    assert "hiraku record update sep10/cpu-test" in readme
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

    loop = LOOP.read_text(encoding="utf-8")
    assert loop.startswith("---\nid: autoresearch\n")
    assert "hiraku status --json" in loop
    assert "hiraku record tree" in loop
    assert "hiraku leaderboard runs --record <id> --json" in loop
    assert "--commit HEAD" in loop
    assert "Never rebind a completed attempt" in loop
    assert "## Finishing" in loop
    assert "results.tsv" not in loop
    assert "hiraku program" not in loop


def test_autoresearch_overlay_initializes_record(tmp_path, monkeypatch):
    project, _ = create_overlay_project(tmp_path, monkeypatch)
    task = Project.discover(project).vault.load("sep10")

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
    mcp_config = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp_config["mcpServers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp", "serve"],
    }
    codex_config = (project / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'command = "hiraku"' in codex_config
    assert "/runs.csv" in gitignore
    assert (project / "program.md").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "program.md"
    ]
    installed = project / "hiraku/workflows/collaborative"
    assert (installed / "workflow.py").read_text(encoding="utf-8") == (
        WORKFLOW / "workflow.py"
    ).read_text(encoding="utf-8")
    assert not (project / "hiraku/programs").exists()
    task_template = (OVERLAY / "task.md").read_text(encoding="utf-8")
    assert (project / "task.md").read_text(encoding="utf-8") == task_template
    assert task.markdown == task_template.strip()
    assert (
        Project.discover(project).vault.file_path(task.id)
        == project / "hiraku/records/sep10.md"
    )
    assert task.branch == "autoresearch/sep10"
    assert task.goal == (
        "Find the best architecture, optimizer, and hyperparameters to minimize "
        "`val_bpb` within the fixed 5-minute training budget."
    )
    assert task.type == "task"
    assert not hasattr(task, "command")
    config = Project.discover(project)
    assert config.default_dispatch == "autoresearch.sh"
    assert "autoresearch" in workflow_ids(project=config)
    assert "collaborative" in workflow_ids(project=config)
    response = start_workflow("autoresearch", project=config)
    assert response["status"] == "completed"
    workflow_md = (project / "hiraku/workflows/autoresearch/workflow.md").read_text()
    assert response["result"] == workflow_md.split("---", 2)[-1].strip()
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["hiraku"]
    assert (project / "hiraku/dispatch/autoresearch.sh").stat().st_mode & 0o111
    assert git(project, "status", "--porcelain") == ""


@LINUX_ONLY
def test_autoresearch_cpu_mock_tracks_dispatch(tmp_path, monkeypatch, capfd):
    project, runner = create_overlay_project(tmp_path, monkeypatch)
    invoke(
        runner,
        [
            "record",
            "create",
            "sep10/cpu-test",
            "--title",
            "CPU smoke test",
            "--type",
            "experiment",
            "--parent",
            "sep10",
            "--commit",
            "HEAD",
        ],
    )
    git(project, "add", "hiraku")
    git(project, "commit", "-m", "add cpu test record")
    commit = git(project, "rev-parse", "HEAD")

    invoke(
        runner,
        [
            "run",
            "--record",
            "sep10/cpu-test",
            "--dispatch",
            "autoresearch",
            "--",
            sys.executable,
            "cpu_mock.py",
            "--val-bpb",
            "0.9",
        ],
    )

    assert "val_bpb: 0.900000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "leaderboard",
                "runs",
                "--record",
                "sep10/cpu-test",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == Project.discover(project).vault.load("sep10/cpu-test").id
    assert row["commit"] == commit
    assert row["raw"]["git_state"] == "clean"
    assert row["raw"]["status"] == "finished"
    assert float(row["metrics"]["val_bpb"]) == 0.9
    assert row["timestamp"] is not None
    assert row["duration"] >= 0


@LINUX_ONLY
def test_autoresearch_dispatcher_records_crash(tmp_path, monkeypatch, capfd):
    _, runner = create_overlay_project(tmp_path, monkeypatch)
    invoke(
        runner,
        [
            "record",
            "create",
            "sep10/failure",
            "--title",
            "Failure handling",
            "--type",
            "experiment",
            "--parent",
            "sep10",
            "--commit",
            "HEAD",
        ],
    )

    failed = runner.invoke(
        main,
        [
            "run",
            "--record",
            "sep10/failure",
            "--dispatch",
            "autoresearch",
            "--",
            sys.executable,
            "cpu_mock.py",
            "--fail",
        ],
    )

    assert failed.exit_code != 0
    assert "val_bpb: 0.000000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "leaderboard",
                "runs",
                "--record",
                "sep10/failure",
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
        "HIRAKU_TRACKING_ROOT": str(root),
        "HIRAKU_RECORD_ID": "cpu-test",
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
    # The timed-out command ran for the full one-second limit.
    assert float(rows[0]["duration"]) >= 1
    assert rows[0]["metric.val_bpb"] == ""
    assert len({row["id"] for row in rows}) == 2


@LINUX_ONLY
def test_autoresearch_dispatcher_accepts_non_gnu_timeout_version(tmp_path):
    dispatcher = DISPATCHER
    system_timeout = shutil.which("timeout")
    assert system_timeout is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_timeout = fake_bin / "timeout"
    fake_timeout.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--version" ]]; then\n'
        '  echo "timeout 0.1 (uutils coreutils)"\n'
        "  exit 0\n"
        "fi\n"
        f'exec "{system_timeout}" "$@"\n',
        encoding="utf-8",
    )
    fake_timeout.chmod(0o755)

    environment = dispatcher_environment(tmp_path)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    result = subprocess.run(
        [dispatcher, "--", sys.executable, "-c", "print('val_bpb: 0.8')"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["status"] == "finished"
    assert rows[0]["metric.val_bpb"] == "0.8"


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
