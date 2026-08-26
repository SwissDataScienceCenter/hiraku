from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .database import Database
from .errors import HirakuError
from .git import GitAdapter

PROJECT_CONFIG = """version: 1
state:
  backend: sqlite
  path: .hiraku/state.db
reports:
  path: .hiraku/reports
"""

LOCAL_GITIGNORE = """state.db
state.db-shm
state.db-wal
reports/
run-metrics/
"""


def initialize(root: Path) -> list[Path]:
    root = root.resolve()
    git = GitAdapter(root)
    git.ensure_repository()
    config_path = root / ".hiraku.yml"
    if config_path.exists():
        raise HirakuError("Hiraku is already initialized in this repository.")
    created: list[Path] = []
    config_path.write_text(PROJECT_CONFIG, encoding="utf-8")
    created.append(config_path)
    local = root / ".hiraku"
    local.mkdir(parents=True, exist_ok=True)
    ignore = local / ".gitignore"
    ignore.write_text(LOCAL_GITIGNORE, encoding="utf-8")
    created.append(ignore)
    (local / "reports").mkdir(exist_ok=True)
    (local / "run-metrics").mkdir(exist_ok=True)
    skill_source = files("hiraku.assets.skill")
    skill_destination = root / ".agents" / "skills" / "hiraku"
    skill_destination.mkdir(parents=True, exist_ok=True)
    for name in ("SKILL.md", "guided.md", "auto.md"):
        destination = skill_destination / name
        destination.write_text(skill_source.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
        created.append(destination)
    Database(local / "state.db").migrate()
    return created
