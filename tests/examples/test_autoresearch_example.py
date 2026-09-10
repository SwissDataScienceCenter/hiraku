from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.data import load_task

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "autoresearch"
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
    monkeypatch.chdir(project)
    runner = CliRunner()
    invoke(runner, ["init"])

    shutil.copy2(EXAMPLE / "program.md", project / "program.md")
    shutil.copy2(EXAMPLE / "task.md", project / "task.md")
    shutil.copy2(EXAMPLE / "cpu_mock.py", project / "cpu_mock.py")
    dispatcher = project / "hiraku/dispatch/autoresearch.sh"
    shutil.copy2(EXAMPLE / "hiraku/dispatch/autoresearch.sh", dispatcher)
    dispatcher.chmod(dispatcher.stat().st_mode | 0o111)
    config = HirakuConfig.discover(project)
    config.default_dispatch = "autoresearch.sh"
    config.save()
    git(project, "add", ".")
    git(project, "commit", "-m", "add Hiraku autoresearch workflow")

    invoke(
        runner,
        [
            "task",
            "create",
            "autoresearch",
            "--goal",
            "Minimize val_bpb",
            "--markdown-file",
            "task.md",
            "--set",
            "metrics=[{name: val_bpb, direction: minimize}]",
            "--set",
            f"entrypoints={{gpu: {{command: [uv, run, python, train.py]}}, "
            f"cpu-mock: {{command: [{sys.executable}, cpu_mock.py], "
            "args: {val-bpb: 1.0}}}",
        ],
    )
    git(project, "switch", "hiraku/tasks/autoresearch")
    git(project, "add", "hiraku.yml", "hiraku/tasks/autoresearch/task.md")
    git(project, "commit", "-m", "create autoresearch task")
    return project, runner


def test_autoresearch_is_an_overlay() -> None:
    expected = {
        "README.md",
        "program.md",
        "task.md",
        "cpu_mock.py",
        "hiraku/dispatch/autoresearch.sh",
    }
    actual = {
        str(path.relative_to(EXAMPLE))
        for path in EXAMPLE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert actual == expected
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert "git clone https://github.com/karpathy/autoresearch.git" in readme
    assert "git checkout" not in readme
    assert "--markdown-file task.md" in readme
    program = (EXAMPLE / "program.md").read_text(encoding="utf-8")
    assert "git reset --hard <reset-point>" in program
    assert "Choose the history mode" not in program


def test_autoresearch_overlay_initializes_task(tmp_path, monkeypatch):
    project, _ = create_overlay_project(tmp_path, monkeypatch)
    task = load_task("autoresearch", config=HirakuConfig.discover(project))

    assert (project / "README.md").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "README.md"
    ]
    assert (project / "prepare.py").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "prepare.py"
    ]
    assert (project / "train.py").read_text(encoding="utf-8") == UPSTREAM_FILES[
        "train.py"
    ]
    assert (project / ".gitignore").read_text(encoding="utf-8") == UPSTREAM_FILES[
        ".gitignore"
    ]
    assert (project / "program.md").read_text(encoding="utf-8") == (
        EXAMPLE / "program.md"
    ).read_text(encoding="utf-8")
    assert task.markdown == (EXAMPLE / "task.md").read_text(encoding="utf-8")
    assert task.entrypoints["gpu"].command == ["uv", "run", "python", "train.py"]
    assert task.entrypoints["cpu-mock"].command[-1] == "cpu_mock.py"
    assert HirakuConfig.discover(project).default_dispatch == "autoresearch.sh"
    assert (project / "hiraku/dispatch/autoresearch.sh").stat().st_mode & 0o111
    assert git(project, "status", "--porcelain") == ""


def test_autoresearch_cpu_mock_tracks_dispatch(tmp_path, monkeypatch, capfd):
    project, runner = create_overlay_project(tmp_path, monkeypatch)
    commit = git(project, "rev-parse", "HEAD")
    invoke(runner, ["experiment", "create", "contract", "--commit", "HEAD"])

    invoke(
        runner,
        ["launch", "contract:cpu-mock", "--", "--val-bpb", "0.9"],
    )

    assert "val_bpb: 0.900000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--task",
                "autoresearch",
                "--experiment",
                "contract",
                "--json",
            ],
        ).output
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "autoresearch"
    assert row["experiment_id"] == "contract"
    assert row["commit"] == commit
    assert row["entrypoint"] == "cpu-mock"
    assert row["raw"]["git_state"] == "dirty"
    assert row["raw"]["status"] == "finished"
    assert float(row["metrics"]["val_bpb"]) == 0.9


def test_autoresearch_dispatcher_records_crash(tmp_path, monkeypatch, capfd):
    _, runner = create_overlay_project(tmp_path, monkeypatch)
    invoke(runner, ["experiment", "create", "failure", "--commit", "HEAD"])

    failed = runner.invoke(main, ["launch", "failure:cpu-mock", "--", "--fail"])

    assert failed.exit_code != 0
    assert "val_bpb: 0.000000" in capfd.readouterr().out
    rows = json.loads(
        invoke(
            runner,
            [
                "run",
                "list",
                "--task",
                "autoresearch",
                "--experiment",
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
        "HIRAKU_TASK_ID": "autoresearch",
        "HIRAKU_EXPERIMENT_ID": "contract",
        "HIRAKU_ENTRYPOINT": "test",
    }


def test_autoresearch_dispatcher_records_timeout_and_invalid_output(tmp_path):
    dispatcher = EXAMPLE / "hiraku/dispatch/autoresearch.sh"
    environment = dispatcher_environment(tmp_path)
    environment["HIRAKU_AUTORESEARCH_TIMEOUT_SECONDS"] = "0.05"
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


def test_autoresearch_dispatcher_rejects_incompatible_csv(tmp_path):
    dispatcher = EXAMPLE / "hiraku/dispatch/autoresearch.sh"
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
        [sys.executable, str(EXAMPLE / "cpu_mock.py")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "val_bpb: 1.000000" in result.stdout
    assert not (tmp_path / "runs.csv").exists()
