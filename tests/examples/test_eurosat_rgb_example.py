from __future__ import annotations

from pathlib import Path

from hiraku.config import HirakuConfig

REPOSITORY = Path(__file__).parents[2]
EXAMPLE = REPOSITORY / "src/hiraku/resources/examples/eurosat-rgb"


def test_eurosat_rgb_example_initializes_without_downloading_data(
    tmp_path: Path,
) -> None:
    project = tmp_path / "eurosat-rgb"
    config = HirakuConfig.init(project, example="eurosat-rgb")

    assert config.default_program == "eurosat-autoresearch"
    assert (project / "train.py").is_file()
    assert (project / "pyproject.toml").is_file()
    assert "project: eurosat-rgb" in (project / "hiraku.yml").read_text()
    assert "/data/" in (project / ".gitignore").read_text()
    assert not (project / "data/eurosat").exists()
    assert (config.programs_dir / "eurosat-autoresearch/program.md").read_text() == (
        EXAMPLE / "src/hiraku/programs/eurosat-autoresearch/program.md"
    ).read_text()


def test_eurosat_trainer_leaves_model_implementation_to_experiments() -> None:
    source = (EXAMPLE / "src/train.py").read_text(encoding="utf-8")

    assert "datasets.EuroSAT(" in source
    assert "download=True" in source
    assert "class FlattenedPixelMLP" not in source
    assert 'choices=["mlp"]' not in source
    assert "Model {model_name!r} is not implemented" in source
    assert "ShallowCNN" not in source
    assert "RandomForest" not in source
    assert "processed.cleveland" not in source
    assert "DEFAULT_EPOCHS = 10" in source
    assert "train_one_epoch(" in source
    assert '"--epochs"' in source
    assert "with wandb.init(" in source
    assert "run.log(metrics, step=epoch)" in source
    assert "wandb.init(" in source
    assert 'project=os.environ.get("WANDB_PROJECT", "eurosat-rgb")' in source
