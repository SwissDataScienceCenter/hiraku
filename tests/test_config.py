from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

import hiraku.project
from hiraku.config import HirakuConfig
from hiraku.errors import (
    ProjectExistsError,
    ProjectNotFoundError,
    ValidationError,
)
from hiraku.record import Record


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )


def test_init_can_require_git_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not in a Git repository"):
        HirakuConfig.init(tmp_path, ignore_git=False)
    assert not (tmp_path / "hiraku.yml").exists()

    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    nested = repository / "nested"
    nested.mkdir()
    with pytest.raises(ValidationError, match="Git repository root"):
        HirakuConfig.init(nested, ignore_git=False)

    config = HirakuConfig.init(nested)
    assert config.root == nested


def test_init_discovery_resources_and_skill_opt_out(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = HirakuConfig.init(root)
    nested = root / "src" / "module.py"
    nested.parent.mkdir()
    nested.write_text("")

    assert HirakuConfig.discover(root).root == root
    assert HirakuConfig.discover(nested).root == root
    assert (config.programs_dir / "interactive/program.md").is_file()
    assert (config.programs_dir / "autoresearch/program.md").is_file()
    assert (config.programs_dir / "literature-review/program.md").is_file()
    assert (config.actions_dir / "record/propose/action.md").is_file()
    for name in ("launch.sh", "worktree.sh"):
        assert (config.dispatch_dir / name).stat().st_mode & 0o111
    source_skill = Path(__file__).parents[1] / ".ai/skills/hiraku/SKILL.md"
    codex_skill = root / ".agents/skills/hiraku/SKILL.md"
    claude_skill = root / ".claude/skills/hiraku/SKILL.md"
    assert source_skill.is_file()
    assert (root / ".agents/skills/hiraku/references").is_dir()
    assert (root / ".claude/skills/hiraku/references").is_dir()
    assert not (root / ".codex/skills").exists()
    assert not (root / ".pi/skills").exists()
    assert codex_skill.is_file()
    assert claude_skill.is_file()
    assert codex_skill.read_text() == source_skill.read_text()
    assert claude_skill.read_text() == source_skill.read_text()
    for skill in (Path(__file__).parents[1] / ".ai/skills").iterdir():
        if skill.is_dir():
            installed_skill = skill / "SKILL.md"
            if installed_skill.is_file():
                codex_copy = root / ".agents/skills" / skill.name / "SKILL.md"
                claude_copy = root / ".claude/skills" / skill.name / "SKILL.md"
                assert codex_copy.read_text() == installed_skill.read_text()
                assert claude_copy.read_text() == installed_skill.read_text()
    assert not (root / ".ai").exists()
    codex_config = tomllib.loads((root / ".codex/config.toml").read_text())
    assert codex_config["mcp_servers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp"],
    }
    expected_server = {"command": "hiraku", "args": ["mcp"]}
    claude_config = json.loads((root / ".mcp.json").read_text())
    pi_config = json.loads((root / ".pi/mcp.json").read_text())
    assert claude_config["mcpServers"]["hiraku"] == expected_server
    assert pi_config["mcpServers"]["hiraku"] == expected_server
    assert config.active_record_id is None

    without_skill = tmp_path / "without-skill"
    HirakuConfig.init(without_skill, install_skill=False)
    assert not (without_skill / ".agents").exists()
    assert not (without_skill / ".claude").exists()
    assert not (without_skill / ".ai").exists()
    assert not (without_skill / ".pi/skills").exists()
    assert (without_skill / ".codex/config.toml").is_file()
    assert (without_skill / ".mcp.json").is_file()
    assert (without_skill / ".pi/mcp.json").is_file()

    with pytest.raises(ProjectExistsError):
        HirakuConfig.init(root)
    with pytest.raises(ProjectNotFoundError):
        HirakuConfig.discover(tmp_path / "missing")


def test_init_sets_tracking_backend(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, tracking_backend="mlflow")

    assert config.tracking_backend == {"type": "mlflow"}
    assert yaml.safe_load(config.path.read_text())["tracking_backend"] == {
        "type": "mlflow"
    }


def test_init_adds_hiraku_to_existing_codex_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    codex_config = root / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text('[mcp_servers.docs]\ncommand = "docs-mcp"\nargs = []\n')

    HirakuConfig.init(root)

    settings = tomllib.loads(codex_config.read_text())
    assert settings["mcp_servers"]["docs"] == {"command": "docs-mcp", "args": []}
    assert settings["mcp_servers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp"],
    }


@pytest.mark.parametrize("relative_path", [Path(".mcp.json"), Path(".pi/mcp.json")])
def test_init_adds_hiraku_to_existing_json_mcp_config(
    tmp_path: Path, relative_path: Path
) -> None:
    root = tmp_path / "project"
    config_path = root / relative_path
    config_path.parent.mkdir(parents=True)
    original = {
        "mcpServers": {"docs": {"command": "docs-mcp", "args": []}},
        "custom": {"setting": True},
    }
    config_path.write_text(json.dumps(original))

    HirakuConfig.init(root)

    settings = json.loads(config_path.read_text())
    assert settings["mcpServers"]["docs"] == original["mcpServers"]["docs"]
    assert settings["mcpServers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp"],
    }
    assert settings["custom"] == original["custom"]


def test_init_preserves_existing_hiraku_codex_server(tmp_path: Path) -> None:
    root = tmp_path / "project"
    codex_config = root / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True)
    original = '[mcp_servers.hiraku]\ncommand = "custom-hiraku"\nargs = []\n'
    codex_config.write_text(original)

    HirakuConfig.init(root)

    assert codex_config.read_text() == original


def test_init_from_template_applies_arbitrary_overlay(tmp_path: Path) -> None:
    template = tmp_path / "template"
    (template / "custom/dispatch").mkdir(parents=True)
    (template / "empty").mkdir()
    (template / "hiraku.yml").write_text(
        "hiraku_dir: custom\ntracking_backend: wandb\nextension:\n  enabled: true\n"
    )
    launcher = template / "custom/dispatch/launch.sh"
    launcher.write_text("#!/bin/sh\necho template\n")
    launcher.chmod(0o744)
    (template / ".example-dotfile").write_text("included\n")
    (template / "payload.bin").write_bytes(b"\x00\xfftemplate")

    root = tmp_path / "project"
    config = HirakuConfig.init(
        root,
        template=template,
        tracking_backend="mlflow",
        install_skill=False,
    )

    assert config.hiraku_dir == root / "custom"
    assert config.tracking_backend == {"type": "mlflow"}
    assert config.to_dict()["extension"] == {"enabled": True}
    assert launcher.read_text() == (root / "custom/dispatch/launch.sh").read_text()
    assert (root / "custom/dispatch/launch.sh").stat().st_mode & 0o111
    assert (root / "custom/dispatch/worktree.sh").is_file()
    assert (root / ".example-dotfile").read_text() == "included\n"
    assert (root / "payload.bin").read_bytes() == b"\x00\xfftemplate"
    assert (root / "empty").is_dir()


def test_template_rejects_existing_files_before_initialization(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    existing = root / "existing.txt"
    existing.write_text("original")
    template = tmp_path / "template"
    template.mkdir()
    (template / "existing.txt").write_text("replacement")

    with pytest.raises(ValidationError, match="overwrite existing path"):
        HirakuConfig.init(root, template=template)

    assert existing.read_text() == "original"
    assert not (root / "hiraku.yml").exists()
    assert not (root / "hiraku").exists()


def test_template_rejects_symlinks_and_invalid_config(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target")
    linked_template = tmp_path / "linked-template"
    linked_template.mkdir()
    (linked_template / "link").symlink_to(target)
    linked_root = tmp_path / "linked-root"

    with pytest.raises(ValidationError, match="symbolic link"):
        HirakuConfig.init(linked_root, template=linked_template)
    assert not linked_root.exists()

    template_link = tmp_path / "template-link"
    template_link.symlink_to(linked_template, target_is_directory=True)
    with pytest.raises(ValidationError, match="cannot be a symbolic link"):
        HirakuConfig.init(linked_root, template=template_link)
    assert not linked_root.exists()

    invalid_template = tmp_path / "invalid-template"
    invalid_template.mkdir()
    (invalid_template / "hiraku.yml").write_text("hiraku_dir: ../outside\n")
    invalid_root = tmp_path / "invalid-root"

    with pytest.raises(ValidationError, match="parent traversal"):
        HirakuConfig.init(invalid_root, template=invalid_template)
    assert not invalid_root.exists()


def test_template_failure_rolls_back_created_paths(tmp_path: Path, monkeypatch) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "first.txt").write_text("first")
    (template / "second.txt").write_text("second")
    root = tmp_path / "project"
    original_copy = hiraku.project.shutil.copy2

    def fail_on_second(source: Path, destination: Path) -> None:
        if Path(source).parent == template and Path(source).name == "second.txt":
            raise OSError("simulated copy failure")
        original_copy(source, destination)

    monkeypatch.setattr(hiraku.project.shutil, "copy2", fail_on_second)

    with pytest.raises(ValidationError, match="simulated copy failure"):
        HirakuConfig.init(root, template=template)
    assert not root.exists()


def test_init_from_packaged_example_copies_only_src(
    tmp_path: Path, monkeypatch
) -> None:
    resources = tmp_path / "resources"
    for name in ("programs", "actions", "dispatch"):
        (resources / name).mkdir(parents=True)
    example = resources / "examples/demo"
    (example / "src").mkdir(parents=True)
    (example / "README.md").write_text("do not copy")
    (example / "example.yml").write_text("description: Demo\n")
    (example / "src/copied.txt").write_text("copied")
    monkeypatch.setattr(hiraku.project, "files", lambda package: resources)

    root = tmp_path / "project"
    HirakuConfig.init(root, example="demo", install_skill=False)

    assert (root / "copied.txt").read_text() == "copied"
    assert not (root / "README.md").exists()
    assert not (root / "example.yml").exists()
    with pytest.raises(ValidationError, match="available examples: demo"):
        HirakuConfig.init(tmp_path / "unknown", example="missing")


def test_init_rejects_conflicting_template_sources(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()

    with pytest.raises(ValidationError, match="mutually exclusive"):
        HirakuConfig.init(tmp_path / "project", template=template, example="demo")


def test_config_preserves_unknown_keys_and_active_record(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    raw = yaml.safe_load(config.path.read_text())
    raw["extension"] = {"enabled": True}
    config.path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config = HirakuConfig.discover(tmp_path)
    first = Record.create("first", config=config)
    child = Record.create("trial", parent_id=first.id, config=config)
    second = Record.create("second", config=config)

    assert config.active_record_id == second.id
    config.set_active_record(child)
    assert config.active_record_id == "trial"
    config.clear_active_record()
    assert config.active_record_id is None
    assert yaml.safe_load(config.path.read_text())["extension"] == {"enabled": True}


def test_hiraku_dir_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    marker = tmp_path / "hiraku.yml"
    marker.write_text("hiraku_dir: ../outside\n")
    with pytest.raises(ValidationError):
        HirakuConfig.load(marker)

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    marker.write_text("hiraku_dir: linked\n")
    with pytest.raises(ValidationError):
        HirakuConfig.load(marker)
