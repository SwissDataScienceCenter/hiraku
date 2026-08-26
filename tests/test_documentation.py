from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def test_stage_three_documentation_is_linked_and_consistent() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    documents = {
        name: (PROJECT_ROOT / "docs" / name).read_text(encoding="utf-8")
        for name in ("quickstart.md", "tutorial.md", "advanced.md")
    }
    for name in documents:
        assert f"docs/{name}" in readme
    assert "--branch" not in documents["quickstart.md"]
    assert "--branch" not in documents["tutorial.md"]
    assert "hiraku exp create heart-disease:stronger-regularization" in documents["advanced.md"]
    assert "mlflow_tracking_uri" in documents["advanced.md"]
    for text in (readme, *documents.values()):
        assert "val/log_loss" in text
        assert "heart-disease" in text
