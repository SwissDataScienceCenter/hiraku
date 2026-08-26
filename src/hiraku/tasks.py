from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

import yaml

from hiraku.config import CONFIG_NAME, Project, find_root, load_project
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import ensure_branch
from hiraku.records import read_task
from hiraku.util import validate_id


def _copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _copy_tree(item, target)
        elif target.exists():
            if target.read_bytes() != item.read_bytes():
                raise HirakuError(f"Refusing to overwrite customized file {target}.")
        else:
            shutil.copyfile(item, target)


def init_project(start: Path | None = None) -> tuple[Path, list[Path]]:
    root = find_root(start)
    resource_root = Path(str(files("hiraku").joinpath("resources")))
    created: list[Path] = []
    config_path = root / CONFIG_NAME
    if config_path.exists():
        expected = (resource_root / CONFIG_NAME).read_bytes()
        if config_path.read_bytes() != expected:
            # An existing valid project configuration is user-owned.
            load_project(root)
    else:
        shutil.copyfile(resource_root / CONFIG_NAME, config_path)
        created.append(config_path)
    project = load_project(root)
    tasks_existed = project.tasks_dir.exists()
    project.tasks_dir.mkdir(parents=True, exist_ok=True)
    if not tasks_existed:
        created.append(project.tasks_dir)
    before = (
        {path for path in project.templates_dir.rglob("*")}
        if project.templates_dir.exists()
        else set()
    )
    _copy_tree(resource_root / "templates", project.templates_dir)
    created.extend(
        path for path in project.templates_dir.rglob("*") if path not in before and path.is_file()
    )
    skill_dir = root / ".agents" / "skills" / "hiraku"
    before_skill = {path for path in skill_dir.rglob("*")} if skill_dir.exists() else set()
    _copy_tree(resource_root / "skill", skill_dir)
    created.extend(
        path for path in skill_dir.rglob("*") if path not in before_skill and path.is_file()
    )
    return root, created


def create_task(
    project: Project,
    task_id: str,
    template: str | None = None,
    branch: str | None = None,
) -> tuple[Path, str, bool]:
    validate_id(task_id, "task")
    task_dir = project.task_dir(task_id)
    if task_dir.exists():
        raise HirakuError(f"Task {task_id!r} already exists.")
    template_name = template or project.default_template
    validate_id(template_name, "template")
    source = project.templates_dir / template_name
    required = [source / "task.md", source / "program.md", source / "launch.yml"]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise HirakuError(
            f"Template {template_name!r} is incomplete; missing {', '.join(sorted(missing))}."
        )
    branch_name = branch or f"hiraku/task/{task_id}"
    if not branch_name.strip() or any(character.isspace() for character in branch_name):
        raise ValidationError(f"Invalid branch name {branch_name!r}.")
    task_dir.mkdir(parents=True)
    try:
        for name in ("task.md", "program.md", "launch.yml"):
            text = (source / name).read_text(encoding="utf-8")
            title = task_id.replace("-", " ").replace("_", " ").title()
            text = (
                text.replace("{{id}}", task_id)
                .replace("{{branch}}", branch_name)
                .replace("{{title}}", title)
            )
            (task_dir / name).write_text(text, encoding="utf-8")
        (task_dir / "experiments").mkdir()
        # Validate copied front matter before creating the branch association.
        read_task(project, task_id)
        created_branch = ensure_branch(project.root, branch_name)
    except Exception:
        shutil.rmtree(task_dir)
        raise
    return task_dir, branch_name, created_branch


def validate_launch_stub(path: Path) -> None:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid launch template {path}: {exc}") from exc
    if not isinstance(value, dict) or "entrypoints" not in value:
        raise ValidationError(f"Launch template {path} must define an entrypoints mapping.")
