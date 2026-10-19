from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.record import load_record


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


def fake_snakemake(tmp_path: Path) -> Path:
    path = tmp_path / "snakemake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if '--dry-run' in sys.argv:\n"
        "    print('localrule all:\\n    input: greeting.txt\\n\\n"
        "rule greeting:\\n    input: data/message.txt\\n    output: greeting.txt')\n"
        "else:\n"
        "    pathlib.Path('greeting.txt').write_text("
        "pathlib.Path('data/message.txt').read_text())\n"
    )
    path.chmod(0o755)
    return path


def test_snakemake_example_records_a_run(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "workflow"
    project.mkdir()
    monkeypatch.chdir(project)
    git(project, "init", "-b", "main")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "user.name", "Test")
    runner = CliRunner()

    invoke(runner, ["init", "--example", "snakemake"])
    assert (project / "Snakefile").is_file()
    assert (project / "data" / "message.txt").is_file()

    invoke(
        runner,
        [
            "record",
            "create",
            "workflow",
            "--type",
            "experiment",
            "--set",
            "tracking_backend={type: snakemake}",
        ],
    )
    invoke(
        runner,
        [
            "snakemake",
            "run",
            "--record",
            "workflow",
            "--snakefile",
            "Snakefile",
            "--command",
            str(fake_snakemake(tmp_path)),
        ],
    )

    config = HirakuConfig.discover(project)
    runs = load_record("workflow", config=config).runs(config=config)
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].declared_inputs == ("data/message.txt", "greeting.txt")
    assert runs[0].declared_outputs == ("greeting.txt",)
    assert [item.path for item in runs[0].outputs] == ["greeting.txt"]
