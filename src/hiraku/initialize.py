from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from hiraku.config import CONFIG_NAME
from hiraku.errors import HirakuError
from hiraku.git import top_level

GENERATED_ASSETS = {
    Path(CONFIG_NAME): "project.yml",
    Path("experiments/template.md"): "experiment-template.md",
    Path(".hiraku/.gitignore"): "hiraku.gitignore",
    Path(".hiraku/policies/guided.md"): "policies/guided.md",
    Path(".hiraku/policies/auto.md"): "policies/auto.md",
    Path(".agents/skills/hiraku/SKILL.md"): "skill/SKILL.md",
    Path(".agents/skills/hiraku/references/create.md"): "skill/references/create.md",
    Path(".agents/skills/hiraku/references/implement.md"): "skill/references/implement.md",
    Path(".agents/skills/hiraku/references/launch-analyze.md"): (
        "skill/references/launch-analyze.md"
    ),
    Path(".agents/skills/hiraku/references/auto-loop.md"): "skill/references/auto-loop.md",
}


def initialize_project(cwd: Path | None = None) -> list[Path]:
    root = (cwd or Path.cwd()).resolve()
    if top_level(root) != root:
        raise HirakuError("hiraku init must be run from the Git repository root.")

    targets = [root / relative for relative in GENERATED_ASSETS]
    collisions = [path for path in targets if path.exists() or path.is_symlink()]
    for target in targets:
        parent = target.parent
        while parent != root:
            if parent.exists() and not parent.is_dir():
                collisions.append(parent)
                break
            parent = parent.parent
    if collisions:
        rendered = ", ".join(str(path.relative_to(root)) for path in sorted(set(collisions)))
        raise HirakuError(f"Initialization would overwrite or conflict with: {rendered}")

    asset_root = files("hiraku").joinpath("assets")
    contents = {
        root / relative: asset_root.joinpath(asset).read_text(encoding="utf-8")
        for relative, asset in GENERATED_ASSETS.items()
    }
    for target, content in contents.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return targets
