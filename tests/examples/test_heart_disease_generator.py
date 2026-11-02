from __future__ import annotations

import runpy
import sys
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku import HirakuConfig
from hiraku.cli import main
from hiraku.record import Record, list_records, open_workspace


@pytest.fixture
def generator():
    path = (
        Path(__file__).parents[2] / "src/hiraku/resources/examples/"
        "heart-disease/generate_results.py"
    )
    return runpy.run_path(str(path))


def test_generator_refuses_existing_output(generator, tmp_path):
    with pytest.raises(FileExistsError):
        generator["generate_results"](tmp_path)


def test_init_with_generator_and_existing_project(tmp_path):
    runner = CliRunner()
    args = ["example", "create", "heart-disease", str(tmp_path)]
    assert runner.invoke(main, [*args, "--with-generator"]).exit_code == 0
    assert (tmp_path / "generate_results.py").is_file()
    assert not (tmp_path / "runs.csv").exists()
    assert runner.invoke(main, [*args, "--generate"]).exit_code == 1
    assert not (tmp_path / "runs.csv").exists()


@pytest.mark.parametrize("copy_generator", [False, True])
def test_init_generates_once(tmp_path, monkeypatch, copy_generator):
    import hiraku.cli as cli

    roots = []
    monkeypatch.setattr(
        cli.runpy,
        "run_path",
        lambda path: {"generate": lambda config: roots.append(config.root)},
    )
    flags = ["--with-generator"] if copy_generator else []
    result = CliRunner().invoke(
        main,
        [
            "example",
            "create",
            "heart-disease",
            str(tmp_path),
            "--generate",
            *flags,
        ],
    )
    assert result.exit_code == 0, result.output
    assert roots == [tmp_path]
    assert (tmp_path / "generate_results.py").exists() == copy_generator


@pytest.mark.parametrize(
    "arguments",
    [
        ["init", "--generate"],
        ["init", "--with-generator"],
        ["example", "create", "minimal", "--generate"],
        ["example", "create", "minimal", "--with-generator"],
        ["example", "create", "heart-disease", "--with-results", "--generate"],
    ],
)
def test_invalid_generation_options_leave_no_project(tmp_path, arguments):
    result = CliRunner().invoke(main, [*arguments, str(tmp_path)])
    assert result.exit_code != 0
    assert not (tmp_path / "hiraku.yml").exists()


def test_init_generate_dispatch_and_failure_keeps_project(tmp_path, monkeypatch):
    import hiraku.cli as cli

    def fail(config):
        (config.root / "completed.txt").write_text("completed")
        raise RuntimeError("failed run")

    monkeypatch.setattr(cli.runpy, "run_path", lambda path: {"generate": fail})
    result = CliRunner().invoke(
        main,
        [
            "example",
            "create",
            "heart-disease",
            str(tmp_path),
            "--generate",
        ],
    )
    assert result.exit_code == 1
    assert "completed runs kept" in result.output
    assert (tmp_path / "hiraku.yml").is_file()
    assert (tmp_path / "completed.txt").is_file()
    assert not (tmp_path / "generate_results.py").exists()


def test_init_generation_publishes_isolated_export(tmp_path, monkeypatch):
    import hiraku.cli as cli

    def export(output):
        generated = HirakuConfig.init(
            output, install_skill=False, records_path="hiraku/records"
        )
        workspace = open_workspace(generated)
        _created_record = Record.create(
            workspace, "generated/generated.md", id="generated"
        )
        generated.set_active_record(_created_record)
        (output / "runs.csv").write_text("id,record_id\nrun,generated\n")

    monkeypatch.setattr(
        cli.runpy, "run_path", lambda path: {"generate_results": export}
    )
    result = CliRunner().invoke(
        main,
        [
            "example",
            "create",
            "heart-disease",
            str(tmp_path),
            "--generate",
        ],
    )
    assert result.exit_code == 0, result.output
    config = HirakuConfig.discover(tmp_path)
    assert config.active_record_id == "generated"
    assert (
        Record.load(open_workspace(config), "generated").runs(config=config)[0].id
        == "run"
    )
    assert not (tmp_path / ".git").exists()


def test_generator_does_not_publish_failed_batch(generator, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("training failed")

    monkeypatch.setitem(
        generator["generate_results"].__globals__, "execute_command", fail
    )
    output = tmp_path / "results"
    with pytest.raises(RuntimeError, match="training failed"):
        generator["generate_results"](output)
    assert not output.exists()


def test_generator_trains_and_exports_overlay(generator, tmp_path, monkeypatch):
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
    monkeypatch.setenv("UV_NO_SYNC", "1")
    output = tmp_path / "results"
    generator["generate_results"](output)
    assert {
        p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()
    } == {
        "hiraku.yml",
        "runs.csv",
        *(
            f"hiraku/records/heart-disease/{name}.md"
            for name in (
                "heart-disease",
                "logistic-baseline",
                "random-forest",
                "forest-tuning",
            )
        ),
    }
    config = HirakuConfig.init(
        tmp_path / "project",
        template=output,
        install_skill=False,
        records_path="hiraku/records",
    )
    assert len(list_records(open_workspace(config))) == 4
    record = Record.load(open_workspace(config), "random-forest")
    runs = record.resolve_tracking_backend(config=config).list_runs()
    assert Counter(r.record_id for r in runs) == {
        "logistic-baseline": 3,
        "random-forest": 3,
        "forest-tuning": 3,
    }
    assert all(r.id and r.timestamp for r in runs)
    assert all(r.decision is None for r in list_records(open_workspace(config)))


def test_validation_rejects_incomplete_results(generator, tmp_path):
    source = (
        Path(__file__).parents[2] / "src/hiraku/resources/examples/"
        "heart-disease/src/train.py"
    )
    (tmp_path / "train.py").write_text(source.read_text())
    (tmp_path / "runs.csv").write_text("id,record_id\n")
    with pytest.raises(ValueError, match="header"):
        generator["validate_runs"](tmp_path)
