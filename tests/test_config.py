from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

import hiraku.project
from hiraku.errors import (
    ProjectExistsError,
    ProjectNotFoundError,
    ValidationError,
)
from hiraku.project import Project
from hiraku.record import Record


def test_project_create_can_skip_mcp_configuration(tmp_path: Path) -> None:
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)

    assert project.tracking_backend == "csv"
    assert project.active_record_id is None
    assert project.records_dir == tmp_path / "hiraku" / "records"
    assert (tmp_path / ".hiraku").is_dir()
    assert not (tmp_path / ".mcp.json").exists()
    assert not (tmp_path / ".codex/config.toml").exists()


def test_project_uses_pydantic_validation_and_forbids_unknown_settings(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    marker = tmp_path / "hiraku/hiraku.yml"
    marker.parent.mkdir()
    marker.write_text("unknown_setting: true\n")

    assert isinstance(project, BaseModel)
    with pytest.raises(ValidationError, match="Invalid project configuration"):
        Project.load(marker)


def test_project_validates_settings_on_creation(tmp_path: Path) -> None:
    with pytest.raises(PydanticValidationError, match="parent traversal"):
        Project(tmp_path, hiraku_dir_name="../outside")

    with pytest.raises(PydanticValidationError, match="default_dispatch"):
        Project(tmp_path, default_dispatch="")


def test_init_leaves_client_hooks_out_of_the_project(tmp_path):
    existing = {"matcher": "Write", "hooks": [{"type": "command", "command": "check"}]}
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        path = tmp_path / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps({"other": True, "hooks": {"PreToolUse": [existing]}})
        )
    Project.create(tmp_path, install_skill=False)
    for relative in (".codex/hooks.json", ".claude/settings.json"):
        path = tmp_path / relative
        assert json.loads(path.read_text()) == {
            "other": True,
            "hooks": {"PreToolUse": [existing]},
        }


@pytest.mark.parametrize("locked", [False, True])
def test_init_uses_uv_when_the_project_has_a_lockfile(tmp_path, locked):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["hiraku"]\n'
    )
    if locked:
        (tmp_path / "uv.lock").touch()
    launcher = ["uv", "run", "hiraku"] if locked else ["hiraku"]
    Project.create(tmp_path, install_skill=False)
    server = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["hiraku"]
    assert [server["command"], *server["args"]] == [*launcher, "mcp"]
    codex = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    assert codex["mcp_servers"]["hiraku"] == server
    assert not (tmp_path / ".claude/settings.json").exists()


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"hooks": []}', '{"hooks":{"PreToolUse":{}}}']
)
def test_init_preserves_hook_configuration_without_inspecting_it(tmp_path, content):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(content)
    Project.create(tmp_path, install_skill=False)
    assert path.read_text() == content
    assert (tmp_path / "hiraku/hiraku.yml").is_file()
    assert (tmp_path / ".mcp.json").is_file()


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )


def test_init_without_repository_or_at_nested_directory(tmp_path: Path) -> None:
    config = Project.create(tmp_path / "plain")
    assert config.root == tmp_path / "plain"

    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    nested = repository / "nested"
    config = Project.create(nested)
    assert config.root == nested


def test_init_discovery_resources_and_skill_opt_out(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = Project.create(root)
    nested = root / "src" / "module.py"
    nested.parent.mkdir()
    nested.write_text("")

    assert Project.discover(root).root == root
    assert Project.discover(nested).root == root
    assert sorted(path.name for path in config.workflows_dir.iterdir()) == [
        "collaborative"
    ]
    assert (config.workflows_dir / "collaborative" / "workflow.py").is_file()
    assert (config.workflows_dir / "collaborative" / "workflow.md").is_file()
    assert not (config.hiraku_dir / "programs").exists()
    assert not (config.hiraku_dir / "actions").exists()
    assert config.default_dispatch is None
    assert not config.dispatch_dir.exists()
    codex_skill = root / ".agents/skills/hiraku/SKILL.md"
    claude_skill = root / ".claude/skills/hiraku/SKILL.md"
    assert codex_skill.is_file()
    assert claude_skill.is_file()
    assert not (root / ".codex/skills").exists()
    assert not (root / ".pi/skills").exists()
    assert not (root / ".gemini/skills").exists()
    assert codex_skill.read_text() == claude_skill.read_text()
    assert config.active_record_id is None

    without_skill = tmp_path / "without-skill"
    Project.create(without_skill, install_skill=False)
    assert not (without_skill / ".agents").exists()
    assert not (without_skill / ".claude/skills").exists()
    assert not (without_skill / ".claude/settings.json").exists()

    with pytest.raises(ProjectExistsError):
        Project.create(root)
    with pytest.raises(ProjectNotFoundError):
        Project.discover(tmp_path / "missing")


def test_init_falls_back_to_copy_when_skill_symlinks_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_symlink(self: Path, target: Path, *, target_is_directory: bool = False):
        raise OSError("symbolic links are unavailable")

    monkeypatch.setattr(Path, "symlink_to", reject_symlink)
    root = tmp_path / "project"
    Project.create(root)

    canonical_skill = root / ".agents/skills/hiraku/SKILL.md"
    claude_skill = root / ".claude/skills/hiraku/SKILL.md"
    assert canonical_skill.is_file()
    assert not (root / ".claude/skills/hiraku").is_symlink()
    assert claude_skill.read_text() == canonical_skill.read_text()


def test_project_config_tracking_backend_is_project_wide(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    config.tracking_backend = {"type": "wandb", "project": "research"}
    assert config.tracking_backend == {"type": "wandb", "project": "research"}


def test_init_from_template_applies_overlay(tmp_path: Path) -> None:
    template = tmp_path / "template"
    (template / "custom/dispatch").mkdir(parents=True)
    (template / "empty").mkdir()
    (template / "hiraku").mkdir()
    (template / "hiraku/hiraku.yml").write_text("hiraku_dir: custom\n")
    launcher = template / "custom/dispatch/launch.sh"
    launcher.write_text("#!/bin/sh\necho template\n")
    launcher.chmod(0o744)
    (template / ".example-dotfile").write_text("included\n")
    (template / "payload.bin").write_bytes(b"\x00\xfftemplate")

    root = tmp_path / "project"
    config = Project.create(
        root,
        template=template,
        install_skill=False,
    )

    assert config.hiraku_dir == root / "custom"
    assert launcher.read_text() == (root / "custom/dispatch/launch.sh").read_text()
    assert (root / "custom/dispatch/launch.sh").stat().st_mode & 0o111
    assert not (root / "custom/dispatch/worktree.sh").exists()
    assert (root / ".example-dotfile").read_text() == "included\n"
    assert (root / "payload.bin").read_bytes() == b"\x00\xfftemplate"
    assert (root / "empty").is_dir()


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
    Project.create(root, example="demo", install_skill=False)

    assert (root / "copied.txt").read_text() == "copied"
    assert not (root / "README.md").exists()
    assert not (root / "example.yml").exists()
    with pytest.raises(ValidationError, match="available examples: demo"):
        Project.create(tmp_path / "unknown", example="missing")


def test_init_rejects_conflicting_template_sources(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()

    with pytest.raises(ValidationError, match="mutually exclusive"):
        Project.create(tmp_path / "project", template=template, example="demo")


def test_config_persists_active_record(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    config = Project.discover(tmp_path)
    first = Record.create("first", project=config)
    child = Record.create("trial", parent_id=first.id, project=config)
    second = Record.create("second", project=config)

    assert config.active_record_id == second.id
    config.set_active_record(child)
    assert config.active_record_id == "trial"
    config.clear_active_record()
    assert config.active_record_id is None
    assert Project.load(config.path).active_record_id is None


def test_active_record_is_stored_in_project_config(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False, install_mcp=False)
    first = Record.create("first", project=config)
    config.set_active_record(first)

    assert config.path == tmp_path / "hiraku/hiraku.yml"
    assert "active_record_id: first" in config.path.read_text()
    assert Project.load(config.path).active_record_id == "first"


def test_hiraku_dir_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    marker = tmp_path / "hiraku/hiraku.yml"
    marker.parent.mkdir()
    marker.write_text("hiraku_dir: ../outside\n")
    with pytest.raises(ValidationError):
        Project.load(marker)

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    marker.write_text("hiraku_dir: linked\n")
    with pytest.raises(ValidationError):
        Project.load(marker)


def test_init_adds_hiraku_to_existing_codex_configuration(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "other"\n')
    Project.create(tmp_path, install_skill=False)
    parsed = tomllib.loads(codex.read_text())
    assert parsed["model"] == "x"
    assert parsed["mcp_servers"]["other"] == {"command": "other"}
    assert parsed["mcp_servers"]["hiraku"] == {"command": "hiraku", "args": ["mcp"]}


def test_init_leaves_inline_codex_mcp_servers_unchanged(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    original = 'mcp_servers = { other = { command = "other" } }\n'
    codex.write_text(original)
    with pytest.raises(ValidationError, match=r"\[mcp_servers.hiraku\] table"):
        Project.create(tmp_path, install_skill=False)
    assert codex.read_text() == original
