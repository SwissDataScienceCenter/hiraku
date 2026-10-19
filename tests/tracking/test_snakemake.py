"""Snakemake run capture and storage tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import ProjectRuns, run_rows, run_text
from hiraku.errors import BackendError, ValidationError
from hiraku.record import Record, load_record
from hiraku.tracking.snakemake import SnakemakeBackend, declared_files, execute


def project(tmp_path: Path) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path)
    Record.create(
        "workflow", config=config, tracking_backend={"type": "snakemake"}
    )
    (tmp_path / "Snakefile").write_text("rule all:\n    input: 'out.txt'\n")
    (tmp_path / "in.txt").write_text("hello\n")
    return config


def fake_snakemake(tmp_path: Path) -> Path:
    path = tmp_path / "fake-snakemake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if '--dry-run' in sys.argv:\n"
        "    print('rule make:\\n    input: in.txt\\n    output: out.txt')\n"
        "else:\n"
        "    if 'fail' in sys.argv: sys.exit(1)\n"
        "    pathlib.Path('out.txt').write_text('done')\n"
    )
    path.chmod(0o755)
    return path


def test_capture_success_and_dashboard_files(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = fake_snakemake(tmp_path)
    run, code = execute(
        config, record_id="workflow", snakefile=Path("Snakefile"),
        command=str(command),
    )

    assert code == 0
    assert run.status == "succeeded"
    assert run.declared_inputs == ("in.txt",)
    assert run.declared_outputs == ("out.txt",)
    assert [(item.path, item.state) for item in run.inputs] == [("in.txt", "file")]
    assert [(item.path, item.state) for item in run.outputs] == [("out.txt", "file")]
    stored = SnakemakeBackend(config).list_runs(record_id="workflow")
    assert len(stored) == 1
    assert stored[0].id == run.id
    rows = run_rows(ProjectRuns([load_record("workflow", config=config)], stored))
    text = run_text(rows[0])
    assert "out.txt" in text
    assert "declared_inputs" in text


def test_capture_failure_and_missing_output(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = fake_snakemake(tmp_path)
    run, code = execute(
        config, record_id="workflow", snakefile=Path("Snakefile"),
        targets=("fail",), command=str(command),
    )
    assert code == 1
    assert run.status == "failed"
    assert run.outputs[0].state == "missing"


def test_declaration_parser_ignores_other_messages() -> None:
    text = (
        "rule a:\n    input: one, two\n    output: three\n"
        "Job stats:\n    input: stray"
    )
    assert declared_files(text) == ({"one", "two"}, {"three"})


def test_preview_failure_is_recorded(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = tmp_path / "bad-snakemake"
    command.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n")
    command.chmod(0o755)

    run, code = execute(
        config, record_id="workflow", snakefile=Path("Snakefile"),
        command=str(command),
    )
    assert code == 2
    assert run.status == "failed"
    assert run.inputs == run.outputs == ()
    assert SnakemakeBackend(config).list_runs()[0].id == run.id


def test_missing_declared_input_is_observed(tmp_path: Path) -> None:
    config = project(tmp_path)
    command = tmp_path / "missing-input-snakemake"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if '--dry-run' in sys.argv:\n"
        "    print('rule make:\\n    input: missing.txt\\n    output: out.txt')\n"
        "else:\n"
        "    pathlib.Path('out.txt').write_text('done')\n"
    )
    command.chmod(0o755)

    run, code = execute(
        config, record_id="workflow", snakefile=Path("Snakefile"),
        command=str(command),
    )
    assert code == 0
    assert run.status == "succeeded"
    assert run.declared_inputs == ("missing.txt",)
    assert [(item.path, item.state) for item in run.inputs] == [
        ("missing.txt", "missing")
    ]


def test_invalid_run_metadata_is_rejected(tmp_path: Path) -> None:
    config = project(tmp_path)
    backend = SnakemakeBackend(config)
    backend.path.mkdir(parents=True)
    (backend.path / "bad-state.json").write_text(
        '{"id": "bad-state", "inputs": [{"path": "a", "state": "bogus"}]}'
    )
    with pytest.raises(BackendError, match="Invalid Snakemake run file"):
        backend.list_runs()


def test_invalid_tracking_source_and_record_file(tmp_path: Path) -> None:
    config = project(tmp_path)
    Record.create("csv-workflow", config=config, tracking_backend={"type": "csv"})
    with pytest.raises(ValidationError, match="tracking_backend must be snakemake"):
        execute(config, record_id="csv-workflow", snakefile=Path("Snakefile"))

    backend = SnakemakeBackend(config)
    backend.path.mkdir(parents=True)
    (backend.path / "bad.json").write_text("{}")
    with pytest.raises(BackendError, match="Invalid Snakemake run file"):
        backend.list_runs()
