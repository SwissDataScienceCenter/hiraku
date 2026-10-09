from __future__ import annotations

from pathlib import Path

from hiraku.config import HirakuConfig


def test_eurosat_rgb_example_initializes_without_downloading_data(
    tmp_path: Path,
) -> None:
    project = tmp_path / "eurosat-rgb"
    config = HirakuConfig.init(project, example="eurosat-rgb")

    assert config.tracking_backend == {"type": "wandb", "project": "eurosat-rgb"}
    assert (project / "train.py").is_file()
    assert (project / "pyproject.toml").is_file()
    assert "/data/" in (project / ".gitignore").read_text()
    assert not (project / "data/eurosat").exists()
