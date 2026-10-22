"""Observation of the files a launched entrypoint reads and writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.launch import launch
from hiraku.observe import load_observations
from hiraku.record import Record, load_record

PREPARE = (
    "import pathlib, sys\n"
    "pathlib.Path('data').mkdir(exist_ok=True)\n"
    "text = open('raw.txt').read()\n"
    "pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text(text)\n"
    "with open('runs.csv', 'a') as stream:\n"
    "    stream.write('id\\n')\n"
)


def project(tmp_path: Path) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    (tmp_path / "prepare.py").write_text(PREPARE)
    (tmp_path / "raw.txt").write_text("hello\n")
    Record.create(
        "workflow",
        config=config,
        entrypoints={"prepare": {"command": [sys.executable, "prepare.py"]}},
    )
    return config


def run(config: HirakuConfig, **options) -> int:
    record = load_record("workflow", config=config)
    return launch(
        record.entrypoint("prepare", config=config),
        name="prepare",
        record_id="workflow",
        args={"output": "data/clean.txt"},
        capture_output=True,
        check=False,
        config=config,
        **{"observe": True, **options},
    ).returncode


def test_launch_records_files_read_and_written(tmp_path: Path) -> None:
    config = project(tmp_path)

    assert run(config) == 0

    [observation] = load_observations(config)
    assert observation["record"] == "workflow"
    assert observation["entrypoint"] == "prepare"
    assert observation["arguments"] == ["--output", "data/clean.txt"]
    # The script is named in the command, raw.txt is opened by Python, and the
    # record's own CSV tracking file is not a step file.
    assert observation["inputs"] == ["prepare.py", "raw.txt"]
    assert observation["outputs"] == ["data/clean.txt"]
    ignore = config.hiraku_dir / "snakemake" / ".gitignore"
    assert ignore.read_text() == "*\n"


def test_unchanged_relaunch_keeps_outputs_it_did_not_rewrite(tmp_path: Path) -> None:
    config = project(tmp_path)
    assert run(config) == 0
    (tmp_path / "prepare.py").write_text(
        "import pathlib\n"
        "if not pathlib.Path('data/clean.txt').exists():\n"
        "    raise SystemExit(1)\n"
        "pathlib.Path('extra.txt').write_text(open('raw.txt').read())\n"
    )

    assert run(config) == 0

    [observation] = load_observations(config)
    assert observation["outputs"] == ["data/clean.txt", "extra.txt"]


def test_observation_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = project(tmp_path)

    assert run(config, observe=False) == 0
    assert load_observations(config) == []
    assert not (config.hiraku_dir / "snakemake").exists()
    monkeypatch.setenv("HIRAKU_OBSERVE", "1")
    assert run(config, observe=False) == 0
    assert len(load_observations(config)) == 1


def test_launches_made_by_a_workflow_run_are_not_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = project(tmp_path)
    monkeypatch.setenv("HIRAKU_WORKFLOW_RUN_ID", "snake_1")

    assert run(config) == 0

    assert load_observations(config) == []


def test_failed_launches_are_not_observed(tmp_path: Path) -> None:
    config = project(tmp_path)
    (tmp_path / "raw.txt").unlink()

    assert run(config) != 0

    assert load_observations(config) == []


def test_project_sitecustomize_still_runs(tmp_path: Path) -> None:
    config = project(tmp_path)
    own = tmp_path.parent / f"{tmp_path.name}-site"
    own.mkdir()
    (own / "sitecustomize.py").write_text(
        "import pathlib; pathlib.Path('site.txt').write_text('ran')\n"
    )

    assert run(config, env={"PYTHONPATH": str(own)}) == 0

    assert (tmp_path / "site.txt").read_text() == "ran"
    [observation] = load_observations(config)
    assert json.dumps(observation["inputs"]) == '["prepare.py", "raw.txt"]'
