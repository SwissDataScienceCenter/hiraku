from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku import HirakuConfig
from hiraku.cli import main
from hiraku.errors import ValidationError


def test_base_example_has_no_results(tmp_path: Path):
    config = HirakuConfig.init(tmp_path, example="heart-disease", install_skill=False)
    assert (tmp_path / "train.py").is_file()
    assert not (tmp_path / "runs.csv").exists()
    assert not list(config.records_dir.rglob("*.md"))


@pytest.mark.parametrize("example", [None, "minimal"])
def test_unavailable_results_leave_no_project(tmp_path: Path, example):
    with pytest.raises(ValidationError, match="requires|no results"):
        HirakuConfig.init(tmp_path, example=example, results=True)
    assert not (tmp_path / "hiraku.yml").exists()


def test_results_preserve_existing_files(tmp_path: Path):
    (tmp_path / "runs.csv").write_text("existing")
    with pytest.raises(ValidationError, match="overwrite"):
        HirakuConfig.init(tmp_path, example="heart-disease", results=True)
    assert (tmp_path / "runs.csv").read_text() == "existing"
    assert not (tmp_path / "train.py").exists()


def test_results_configuration_and_explicit_override(tmp_path: Path):
    config = HirakuConfig.init(
        tmp_path,
        example="heart-disease",
        results=True,
        tracking_backend="mlflow",
        install_skill=False,
    )
    assert config.active_record_id == "random-forest"
    assert config.tracking_backend["type"] == "mlflow"


def test_results_override_base_files(tmp_path: Path, monkeypatch):
    from hiraku import project

    resources = tmp_path / "resources"
    base = resources / "examples/layered/src"
    results = resources / "examples/layered/results"
    base.mkdir(parents=True)
    results.mkdir()
    (base / "train.py").write_text("base")
    (base / "hiraku.yml").write_text("branch: custom/{record_id}\n")
    (results / "train.py").write_text("results")
    (results / "hiraku.yml").write_text("active_record_id: root\n")
    monkeypatch.setattr(project, "files", lambda name: resources)
    target = tmp_path / "target"
    config = HirakuConfig.init(
        target, example="layered", results=True, install_skill=False
    )
    assert (target / "train.py").read_text() == "results"
    assert config.branch == "custom/{record_id}"
    assert config.active_record_id == "root"


def test_results_flag_requires_example(tmp_path: Path):
    result = CliRunner().invoke(main, ["init", str(tmp_path), "--with-results"])
    assert result.exit_code == 1
    assert "requires a packaged example" in result.output
