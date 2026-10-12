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
    for workflow in (
        "autoresearch-python",
        "autodataset",
        "guided-research",
        "hiraku-agent",
    ):
        assert (config.workflows_dir / workflow / "workflow.py").is_file()
        assert (config.workflows_dir / workflow / "workflow.md").is_file()
    assert config.mcp is not None
    assert config.mcp.workflows == [
        "autodataset",
        "autoresearch-python",
        "guided-research",
        "hiraku-agent",
    ]
    assert not (config.hiraku_dir / "programs").exists()
    assert not (config.hiraku_dir / "actions").exists()
    for name in ("launch.sh", "worktree.sh"):
        assert (config.dispatch_dir / name).stat().st_mode & 0o111
    codex_skill = root / ".agents/skills/hiraku/SKILL.md"
    claude_skill = root / ".claude/skills/hiraku/SKILL.md"
    assert codex_skill.is_file()
    assert claude_skill.is_file()
    assert codex_skill.read_text() == claude_skill.read_text()
    assert config.active_record_id is None

    without_skill = tmp_path / "without-skill"
    HirakuConfig.init(without_skill, install_skill=False)
    assert not (without_skill / ".agents").exists()
    assert not (without_skill / ".claude").exists()

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
    for name in ("workflows", "dispatch"):
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


def test_legacy_default_program_is_rejected_with_migration_message(
    tmp_path: Path,
) -> None:
    (tmp_path / "hiraku.yml").write_text("default_program: interactive\n")
    with pytest.raises(ValidationError, match="default_program.*replaced by workflows"):
        HirakuConfig.discover(tmp_path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("mcp:\n  workflows: [a, a]\n", "duplicate"),
        ("mcp:\n  workflows: ['bad/id']\n", "Invalid workflow ID"),
        ("mcp:\n  unknown: true\n", "Extra inputs"),
    ],
)
def test_mcp_settings_are_validated(tmp_path: Path, content: str, message: str) -> None:
    (tmp_path / "hiraku.yml").write_text(content)
    with pytest.raises(Exception, match=message):
        HirakuConfig.discover(tmp_path)


def test_mcp_settings_round_trip(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    config.set_mcp_workflows([])
    assert HirakuConfig.discover(tmp_path).mcp.workflows == []
    config.set_mcp_workflows(None)
    reloaded = HirakuConfig.discover(tmp_path)
    assert reloaded.mcp is not None
    assert reloaded.mcp.workflows is None


def test_init_configures_mcp_for_claude_code_and_codex(tmp_path: Path) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    claude = json.loads((tmp_path / ".mcp.json").read_text())
    assert claude == {"mcpServers": {"hiraku": {"command": "hiraku", "args": ["mcp"]}}}
    codex = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert codex["mcp_servers"]["hiraku"] == {
        "command": "hiraku",
        "args": ["mcp"],
    }


def test_init_keeps_existing_harness_configuration(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}})
    )
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text('model = "x"\n')
    HirakuConfig.init(tmp_path, install_skill=False)
    claude = json.loads((tmp_path / ".mcp.json").read_text())
    assert set(claude["mcpServers"]) == {"other", "hiraku"}
    codex = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert codex["model"] == "x"
    assert codex["mcp_servers"]["hiraku"]["command"] == "hiraku"

    existing = tmp_path / "existing"
    existing.mkdir()
    custom = {"mcpServers": {"hiraku": {"command": "uv", "args": ["run", "hiraku"]}}}
    (existing / ".mcp.json").write_text(json.dumps(custom))
    HirakuConfig.init(existing, install_skill=False)
    assert json.loads((existing / ".mcp.json").read_text()) == custom


def test_init_rolls_back_modified_harness_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    original = json.dumps({"mcpServers": {"other": {"command": "other"}}})
    (tmp_path / ".mcp.json").write_text(original)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text("not = [valid\n")
    with pytest.raises(ValidationError, match="Cannot read"):
        HirakuConfig.init(tmp_path, install_skill=False)
    assert (tmp_path / ".mcp.json").read_text() == original
    assert not (tmp_path / "hiraku.yml").exists()
    assert not (tmp_path / "hiraku").exists()
