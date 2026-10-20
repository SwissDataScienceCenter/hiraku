import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig


@pytest.mark.parametrize(
    ("arguments", "destination", "example"),
    [
        (["init"], ".", False),
        (["init", "my-project"], "my-project", False),
        (["clone", "minimal"], "minimal", True),
        (["clone", "minimal", "my-project"], "my-project", True),
        (["clone", "minimal", "."], ".", True),
    ],
)
def test_project_destination(tmp_path, monkeypatch, arguments, destination, example):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    root = (tmp_path / destination).resolve()
    assert HirakuConfig.load(root / "hiraku.yml").root == root
    assert (root / "evaluate.py").is_file() == example
    assert (tmp_path / "hiraku.yml").exists() == (destination == ".")
    if destination == "my-project":
        assert not (tmp_path / "minimal").exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_init_existing_directory_preserves_unrelated_files(
    tmp_path, monkeypatch, absolute
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("keep me")
    monkeypatch.chdir(tmp_path)
    destination = str(root) if absolute else "project"
    result = CliRunner().invoke(main, ["init", destination])
    assert result.exit_code == 0, result.output
    assert (root / "notes.txt").read_text() == "keep me"
    assert (root / "hiraku.yml").is_file()


@pytest.mark.parametrize("destination", [None, ".", "project"])
def test_plain_reinitialization_leaves_project_unchanged(
    tmp_path, monkeypatch, destination
):
    root = tmp_path / "project" if destination == "project" else tmp_path
    config = HirakuConfig.init(root, tracking_backend="mlflow")
    (config.dispatch_dir / "launch.sh").write_text("custom launcher\n")
    (config.dispatch_dir / "worktree.sh").unlink()
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    monkeypatch.chdir(tmp_path)
    arguments = ["init"]
    if destination is not None:
        arguments.append(destination)
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert "already initialized" in result.output
    assert {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize(
    "command",
    [["clone", "minimal"], ["init", "--tracking-backend", "mlflow"]],
)
def test_reinitialization_with_options_is_rejected(tmp_path, command):
    config = HirakuConfig.init(tmp_path)
    before = config.path.read_bytes()
    result = CliRunner().invoke(main, [*command, str(tmp_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert config.path.read_bytes() == before
    assert not (tmp_path / "evaluate.py").exists()


def test_example_collision_leaves_destination_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "minimal"
    root.mkdir()
    (root / "evaluate.py").write_text("existing code\n")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["clone", "minimal"])
    assert result.exit_code == 1
    assert "overwrite" in result.output
    assert list(root.iterdir()) == [root / "evaluate.py"]
    assert (root / "evaluate.py").read_text() == "existing code\n"


@pytest.mark.parametrize("example", ["missing", "../outside"])
def test_invalid_example_creates_no_destination(tmp_path, monkeypatch, example):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["clone", example])
    assert result.exit_code == 1
    assert list(tmp_path.iterdir()) == []


def test_template_keeps_current_directory_default(tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.mkdir()
    (template / "hello.txt").write_text("hello")
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, ["init", "--template", str(template)])
    assert result.exit_code == 0, result.output
    assert (root / "hello.txt").read_text() == "hello"
    assert (root / "hiraku.yml").is_file()


@pytest.mark.parametrize(
    "option", ["--example", "--generate", "--with-results", "--with-generator"]
)
def test_init_rejects_example_options(tmp_path, monkeypatch, option):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", option])
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert not list(tmp_path.iterdir())


def test_clone_can_initialize_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["clone", "minimal", "--git"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "minimal/.git").is_dir()
    assert (tmp_path / "minimal/evaluate.py").is_file()


def test_clone_requires_example_and_rejects_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["clone"]).exit_code == 2
    assert runner.invoke(main, ["clone", "minimal", "--template", "."]).exit_code == 2
    assert not list(tmp_path.iterdir())
