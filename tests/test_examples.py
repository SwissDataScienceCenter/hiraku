from pathlib import Path

import pytest

import hiraku.examples
from hiraku.errors import ValidationError
from hiraku.examples import Example


@pytest.fixture
def example_source(tmp_path: Path, monkeypatch) -> Path:
    examples = tmp_path / "examples"
    source = examples / "demo"
    (source / "outputs/nested").mkdir(parents=True)
    (source / ".hiraku-example.yml").write_text(
        "title: Demo\ndescription: Example guide\n"
        "results: ['outputs/**/*.txt']\nexclude: ['**/draft-*', scratch]\n"
    )
    (source / "hiraku.yml").write_text("hiraku_dir: custom\n")
    (source / "README.md").write_text("Example guide")
    (source / "outputs/nested/kept.txt").write_text("result")
    (source / "outputs/draft-result.txt").write_text("draft")
    (source / "draft-notes.md").write_text("draft")
    (source / "scratch").mkdir()
    (source / "scratch/local.txt").write_text("local")
    monkeypatch.setattr(hiraku.examples, "_examples_root", lambda: examples)
    return source


@pytest.mark.parametrize("with_results", [False, True])
def test_copy_selects_example_files(
    tmp_path: Path, example_source: Path, with_results: bool
) -> None:
    target = tmp_path / "project"
    marker = Example.create("demo", target, with_results=with_results)

    assert marker.read_bytes() == (example_source / "hiraku.yml").read_bytes()
    assert (target / "README.md").is_file()
    assert (target / "outputs/nested/kept.txt").is_file() == with_results
    for path in (
        "outputs/draft-result.txt",
        "draft-notes.md",
        "scratch",
        ".hiraku-example.yml",
    ):
        assert not (target / path).exists()


@pytest.mark.parametrize(
    ("name", "error"),
    [("missing", "available examples: demo"), ("demo", "no results to include")],
)
def test_unavailable_examples_or_results_leave_no_target(
    tmp_path: Path, example_source: Path, name: str, error: str
) -> None:
    (example_source / "outputs/nested/kept.txt").unlink()
    target = tmp_path / "project"

    with pytest.raises(ValidationError, match=error):
        Example.create(name, target, with_results=True)
    assert not target.exists()


def test_invalid_example_glob_leaves_no_target(
    tmp_path: Path, example_source: Path
) -> None:
    (example_source / ".hiraku-example.yml").write_text(
        "title: Demo\ndescription: Example guide\nresults: ['../*.txt']\n"
    )
    target = tmp_path / "project"

    with pytest.raises(ValidationError, match="example-relative glob"):
        Example.create("demo", target)
    assert not target.exists()
