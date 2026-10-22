"""Snakemake workflow generation and execution recording tests."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path
from textwrap import indent

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import BackendError, ValidationError
from hiraku.record import Record, load_record
from hiraku.snakemake import generate_workflow, observed_workflow, run_workflow

SUMMARY = (
    "output_file\tdate\trule\tlog-file(s)\tinput-file(s)\tshellcmd\tstatus\tplan\n"
    "out.txt\t-\tstep__make\t\tin.txt\t"
    "hiraku launch make --record step -- --output out.txt\tok\tupdate pending\n"
)


def project(tmp_path: Path) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path)
    Record.create("workflow", config=config, tracking_backend={"type": "csv"})
    (tmp_path / "Snakefile").write_text("rule all:\n    input: 'out.txt'\n")
    (tmp_path / "in.txt").write_text("hello\n")
    return config


def fake_snakemake(
    tmp_path: Path,
    *,
    summary: str = f"sys.stdout.write({SUMMARY!r})",
    execution: str = "pathlib.Path('out.txt').write_text('done')",
) -> str:
    path = tmp_path / "fake-snakemake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import csv, json, os, pathlib, signal, sys\n"
        "if '--detailed-summary' in sys.argv:\n"
        + indent(summary, "    ")
        + "\nelse:\n"
        + indent(execution, "    ")
        + "\n"
    )
    path.chmod(0o755)
    return str(path)


def run(config: HirakuConfig, command: str, **options) -> tuple[dict, int]:
    return run_workflow(
        config,
        record_id="workflow",
        snakefile=Path("Snakefile"),
        command=command,
        **options,
    )


def test_run_stores_started_then_each_job_with_its_record(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = fake_snakemake(
        tmp_path,
        execution=(
            "rows = list(csv.DictReader(open('runs.csv')))\n"
            "assert [row['outcome'] for row in rows] == ['started']\n"
            "pathlib.Path('env.txt').write_text(os.environ['HIRAKU_WORKFLOW_RUN_ID'])\n"
            "pathlib.Path('out.txt').write_text('done')"
        ),
    )

    row, code = run(config, command, snakemake_config=("make=--fast",))

    assert code == 0
    assert row["outcome"] == "succeeded"
    assert row["git_state"] == "unknown"
    assert (tmp_path / "env.txt").read_text() == row["id"]
    assert row["param.command"].endswith("--cores 1 --config make=--fast")
    # The run is a plain CSV run, readable through the record's backend.
    [stored] = load_record("workflow", config=config).runs(config=config)
    assert stored.id == row["id"]
    assert stored.entrypoint == "snakemake"
    assert len(stored.params["snakefile_sha256"]) == 64
    assert json.loads(stored.raw["jobs"]) == [
        {
            "rule": "step__make",
            "record": "step",
            "entrypoint": "make",
            "command": "hiraku launch make --record step -- --output out.txt",
            "inputs": ["in.txt"],
            "outputs": ["out.txt"],
            "ran": True,
        }
    ]


def test_rerun_without_changes_is_a_no_op(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = fake_snakemake(tmp_path, execution="print('Nothing to be done')")
    (tmp_path / "out.txt").write_text("done")

    row, code = run(config, command)

    assert code == 0
    assert row["outcome"] == "no-op"
    assert json.loads(row["jobs"])[0]["ran"] is False


def test_rules_without_hiraku_launch_keep_their_command(tmp_path: Path) -> None:
    config = project(tmp_path)
    summary = SUMMARY.replace(
        "hiraku launch make --record step -- --output out.txt", "cp in.txt out.txt"
    )
    row, _ = run(config, fake_snakemake(tmp_path, summary=f"print({summary!r})"))

    [job] = json.loads(row["jobs"])
    assert (job["record"], job["entrypoint"], job["command"]) == (
        None,
        None,
        "cp in.txt out.txt",
    )


@pytest.mark.parametrize("phase", ["summary", "execution"])
def test_failure_records_returncode_and_diagnostic(tmp_path: Path, phase: str) -> None:
    config = project(tmp_path)
    failing = "print('cannot run', file=sys.stderr)\nsys.exit(7)"
    row, code = run(config, fake_snakemake(tmp_path, **{phase: failing}))

    assert code == 7
    assert row["outcome"] == "failed"
    assert row["returncode"] == "7"
    assert "cannot run" in row["diagnostic"]
    assert row["ended"]
    assert (json.loads(row["jobs"]) == []) is (phase == "summary")


def test_dry_run_is_planned_not_executed(tmp_path: Path) -> None:
    config = project(tmp_path)
    row, code = run(config, fake_snakemake(tmp_path), dry_run=True)

    assert code == 0
    assert row["outcome"] == "planned"
    assert not (tmp_path / "out.txt").exists()
    assert json.loads(row["jobs"])[0]["outputs"] == ["out.txt"]


def test_interrupted_execution_is_recorded(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = fake_snakemake(tmp_path, execution="os.kill(os.getpid(), signal.SIGTERM)")
    row, code = run(config, command)

    assert code < 0
    assert row["outcome"] == "interrupted"


def test_run_records_the_executed_commit_and_git_state(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    config = project(tmp_path)
    command = fake_snakemake(tmp_path)
    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-m", "init")

    row, _ = run(config, command)
    assert row["commit"] == git("rev-parse", "HEAD")
    assert row["git_state"] == "clean"

    dirty, _ = run(config, command)
    assert dirty["git_state"] == "dirty"

    # Evidence kept with ignored records leaves the code checkout clean.
    Record.create(
        "evidence",
        config=config,
        tracking_backend={"type": "csv", "path": "hiraku/runs.csv"},
    )
    git("rm", "-rq", "--cached", "hiraku")
    (tmp_path / ".gitignore").write_text("hiraku/\nruns.csv\nout.txt\n")
    git("add", "-A")
    git("commit", "-m", "ignore records")
    for _ in range(2):
        clean, _ = run_workflow(
            config, record_id="evidence", snakefile=Path("Snakefile"), command=command
        )
        assert clean["git_state"] == "clean"
    assert (tmp_path / "hiraku" / "runs.csv").exists()


def test_existing_rows_and_columns_are_kept(tmp_path: Path) -> None:
    config = project(tmp_path)
    (tmp_path / "runs.csv").write_text("id,record_id,metric.loss\nr0,workflow,0.5\n")

    row, _ = run(config, fake_snakemake(tmp_path))

    with (tmp_path / "runs.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [item["id"] for item in rows] == ["r0", row["id"]]
    assert rows[0]["metric.loss"] == "0.5"
    runs = load_record("workflow", config=config).runs(config=config)
    assert {item.id for item in runs} == {"r0", row["id"]}


def test_invalid_executions_are_rejected_before_recording(tmp_path: Path) -> None:
    config = project(tmp_path)
    Record.create("tracked", config=config, tracking_backend={"type": "mlflow"})
    with pytest.raises(ValidationError, match="tracking_backend must be csv"):
        run_workflow(config, record_id="tracked", snakefile=Path("Snakefile"))
    with pytest.raises(ValidationError, match="not supported"):
        run(config, "snakemake", command_args=("--directory", "elsewhere"))
    with pytest.raises(ValidationError, match="looks like an option"):
        run(config, "snakemake", targets=("--cores",))
    with pytest.raises(BackendError, match="not found"):
        run(config, "definitely-not-a-snakemake")
    assert not (tmp_path / "runs.csv").exists()


def test_generate_drafts_one_launching_rule_per_subtree_entrypoint(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    record = Record.create(
        "digits",
        config=config,
        entrypoints={"prepare": {"command": ["python", "prepare.py"]}},
    )
    Record.create(
        "weak-l2",
        parent_id="digits",
        config=config,
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    Record.create("other", config=config, entrypoints={"run": {"command": ["python"]}})

    text = generate_workflow(record, config=config)

    assert "rule digits__prepare:" in text
    assert "rule weak_l2__train:" in text
    assert '"hiraku launch train --record weak-l2 -- "' in text
    assert "config.get('train', '')" in text
    assert "other" not in text
    with pytest.raises(ValidationError, match="no entrypoints"):
        generate_workflow(Record.create("empty", config=config), config=config)


def test_cli_generate_prints_writes_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    Record.create(
        "workflow",
        config=config,
        entrypoints={"build": {"command": ["python", "build.py"]}},
    )
    runner = CliRunner()
    arguments = ["snakemake", "generate", "--record", "workflow"]

    printed = runner.invoke(main, [*arguments, "--print"])
    assert printed.exit_code == 0, printed.output
    assert not (tmp_path / "Snakefile").exists()
    written = runner.invoke(main, arguments)
    assert written.exit_code == 0, written.output
    assert (tmp_path / "Snakefile").read_text() == printed.output
    refused = runner.invoke(main, arguments)
    assert refused.exit_code != 0
    assert "already exists" in refused.output


@pytest.mark.integration
def test_real_snakemake_launches_record_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("snakemake")
    if shutil.which("snakemake") is None or shutil.which("hiraku") is None:
        pytest.skip("snakemake and hiraku executables required")
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
    initialized = CliRunner().invoke(main, ["init"])
    assert initialized.exit_code == 0, initialized.output
    config = HirakuConfig.discover(tmp_path)
    Record.create(
        "workflow",
        config=config,
        tracking_backend={"type": "csv"},
        entrypoints={
            "build": {
                "command": [
                    "python",
                    "-c",
                    "import pathlib, sys; pathlib.Path(sys.argv[-1]).write_text('{x}')",
                ]
            }
        },
    )
    (tmp_path / "Snakefile").write_text(
        'rule all:\n    input: "out file.txt"\n\n'
        'rule workflow__build:\n    output: "out file.txt"\n'
        "    shell: \"hiraku launch build --record workflow -- --output '{output}'\"\n"
    )

    row, code = run(config, "snakemake")

    assert code == 0, row["diagnostic"]
    assert (tmp_path / "out file.txt").read_text() == "{x}"
    [job] = json.loads(row["jobs"])
    assert (job["record"], job["entrypoint"], job["ran"]) == ("workflow", "build", True)
    again, _ = run(config, "snakemake")
    assert again["outcome"] == "no-op"


def observe(config: HirakuConfig, record: str, entrypoint: str, **fields) -> None:
    directory = config.hiraku_dir / "snakemake" / "observed"
    directory.mkdir(parents=True, exist_ok=True)
    observation = {"record": record, "entrypoint": entrypoint, "arguments": []}
    observation |= {"inputs": [], "outputs": [], **fields}
    (directory / f"{record}__{entrypoint}.json").write_text(json.dumps(observation))


def test_observed_workflow_declares_the_files_of_each_launch(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    record = Record.create(
        "digits", config=config, tracking_backend={"type": "csv", "path": "x.csv"}
    )
    Record.create("weak-l2", parent_id="digits", config=config)
    Record.create("other", config=config)
    observe(
        config,
        "digits",
        "prepare",
        arguments=["--output", "data/{a}.npz"],
        inputs=["prepare.py", "x.csv"],
        outputs=["data/{a}.npz", "shared.db"],
    )
    observe(
        config,
        "weak-l2",
        "train",
        inputs=["data/{a}.npz", "train.py"],
        outputs=["shared.db"],
    )
    observe(config, "other", "run", outputs=["other.txt"])

    text = observed_workflow(record, config=config)

    # Braces are escaped as literal paths, a file written by several steps is
    # left out, and the workflow's tracking file is not a step file.
    assert (
        "rule digits__prepare:\n"
        '    input:\n        "prepare.py",\n'
        '    output:\n        "data/{{a}}.npz",\n'
        "    shell:\n"
        '        "hiraku launch prepare --record digits -- '
        "--output 'data/{{a}}.npz' \""
        " + config.get('prepare', '')\n"
    ) in text
    log = "hiraku/snakemake/logs/weak_l2__train.log"
    assert f'    output:\n        "{log}",\n' in text
    assert "config.get('train', '') + \" | tee {output}\"" in text
    assert f'rule all:\n    input:\n        "{log}",\n' in text
    assert "other" not in text
    with pytest.raises(ValidationError, match="No observed launches"):
        observed_workflow(Record.create("empty", config=config), config=config)


def test_cli_run_without_snakefile_uses_the_observed_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = project(tmp_path)
    monkeypatch.chdir(tmp_path)
    observe(config, "workflow", "make", inputs=["in.txt"], outputs=["out.txt"])
    command = fake_snakemake(tmp_path)
    runner = CliRunner()
    arguments = ["snakemake", "run", "--record", "workflow", "--command", command]

    hand_written = runner.invoke(main, arguments)
    assert hand_written.exit_code == 0, hand_written.output
    assert "observed" not in hand_written.output
    observed = runner.invoke(main, [*arguments, "--observed"])
    assert observed.exit_code == 0, observed.output
    assert "Generated hiraku/snakemake/Snakefile" in observed.output
    (tmp_path / "Snakefile").unlink()
    implicit = runner.invoke(main, arguments)
    assert implicit.exit_code == 0, implicit.output
    rows = list(csv.DictReader((tmp_path / "runs.csv").open()))
    assert rows[-1]["param.command"].count("hiraku/snakemake/Snakefile") == 1
    both = runner.invoke(main, [*arguments, "--observed", "--snakefile", "S"])
    assert both.exit_code != 0
    assert "mutually exclusive" in both.output

    generated = runner.invoke(
        main, ["snakemake", "generate", "--record", "workflow", "--observed"]
    )
    assert generated.exit_code == 0, generated.output
    assert 'output:\n        "out.txt"' in (tmp_path / "Snakefile").read_text()


@pytest.mark.integration
def test_real_snakemake_runs_observed_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("snakemake")
    if shutil.which("snakemake") is None or shutil.which("hiraku") is None:
        pytest.skip("snakemake and hiraku executables required")
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
    assert CliRunner().invoke(main, ["init"]).exit_code == 0
    config = HirakuConfig.discover(tmp_path)
    (tmp_path / "raw.txt").write_text("hello\n")
    (tmp_path / "prepare.py").write_text(
        "import pathlib\npathlib.Path('clean.txt').write_text(open('raw.txt').read())\n"
    )
    (tmp_path / "train.py").write_text(
        "import pathlib, sys\n"
        "text = open('clean.txt').read()\n"
        "pathlib.Path(sys.argv[-1]).write_text(text.upper())\n"
    )
    Record.create(
        "workflow",
        config=config,
        entrypoints={"prepare": {"command": ["python", "prepare.py"]}},
    )
    Record.create(
        "model",
        parent_id="workflow",
        config=config,
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    runner = CliRunner()
    for arguments in (
        ["launch", "prepare", "--record", "workflow"],
        ["launch", "train", "--record", "model", "--", "--output", "model.txt"],
    ):
        launched = subprocess.run(["hiraku", *arguments], capture_output=True)
        assert launched.returncode == 0, launched.stderr
    run_workflow_cli = ["snakemake", "run", "--record", "workflow"]

    assert "no-op" in runner.invoke(main, run_workflow_cli).output
    (tmp_path / "raw.txt").write_text("changed\n")
    result = runner.invoke(main, run_workflow_cli)

    assert "succeeded" in result.output, result.output
    assert (tmp_path / "model.txt").read_text() == "CHANGED\n"
    [*_, row] = load_record("workflow", config=config).runs(config=config)
    jobs = {job["rule"]: job for job in json.loads(row.raw["jobs"])}
    assert (jobs["model__train"]["record"], jobs["model__train"]["ran"]) == (
        "model",
        True,
    )
    assert jobs["model__train"]["inputs"] == ["clean.txt", "train.py"]
