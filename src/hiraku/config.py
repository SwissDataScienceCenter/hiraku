from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.errors import HirakuError, ValidationError
from hiraku.git import repository_root
from hiraku.util import safe_relative

CONFIG_NAME = ".hiraku.yml"
CONFIG_FIELDS = {
    "tasks_dir",
    "templates_dir",
    "default_template",
    "tracking_backend",
    "mlflow_tracking_db",
    "review_provider",
    "review_remote",
}


@dataclass(frozen=True)
class Project:
    root: Path
    tasks_dir: Path
    templates_dir: Path
    default_template: str
    tracking_backend: str
    mlflow_tracking_db: Path
    review_provider: str | None
    review_remote: str | None

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    def task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def runs_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "runs.csv"

    @property
    def mlflow_tracking_uri(self) -> str:
        return f"sqlite:///{self.mlflow_tracking_db.as_posix()}"


def find_root(start: Path | None = None) -> Path:
    return repository_root((start or Path.cwd()).resolve())


def load_project(start: Path | None = None) -> Project:
    root = find_root(start)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise HirakuError(f"No {CONFIG_NAME} found. Run 'hiraku init' first.")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid {CONFIG_NAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{CONFIG_NAME} must contain a YAML mapping.")
    unknown = set(raw) - CONFIG_FIELDS
    if unknown:
        raise ValidationError(f"Unknown {CONFIG_NAME} fields: {', '.join(sorted(unknown))}.")
    values: dict[str, Any] = {
        "tasks_dir": raw.get("tasks_dir", "tasks"),
        "templates_dir": raw.get("templates_dir", ".hiraku/templates"),
        "default_template": raw.get("default_template", "guided"),
        "tracking_backend": raw.get("tracking_backend", "table"),
        "mlflow_tracking_db": raw.get("mlflow_tracking_db", "mlflow.db"),
    }
    if not all(isinstance(values[key], str) for key in values):
        raise ValidationError(f"All {CONFIG_NAME} values must be strings.")
    if values["tracking_backend"] not in {"table", "mlflow"}:
        raise ValidationError("tracking_backend must be 'table' or 'mlflow'.")
    if "mlflow_tracking_db" in raw and values["tracking_backend"] != "mlflow":
        raise ValidationError("mlflow_tracking_db is valid only with tracking_backend: mlflow.")
    review_provider = raw.get("review_provider")
    review_remote = raw.get("review_remote")
    if review_provider is not None and review_provider not in {"github", "gitlab"}:
        raise ValidationError("review_provider must be 'github' or 'gitlab'.")
    if review_remote is not None and (
        not isinstance(review_remote, str) or not review_remote.strip()
    ):
        raise ValidationError("review_remote must be a non-empty string.")
    return Project(
        root=root,
        tasks_dir=safe_relative(root, values["tasks_dir"], "tasks_dir"),
        templates_dir=safe_relative(root, values["templates_dir"], "templates_dir"),
        default_template=values["default_template"],
        tracking_backend=values["tracking_backend"],
        mlflow_tracking_db=safe_relative(root, values["mlflow_tracking_db"], "mlflow_tracking_db"),
        review_provider=review_provider,
        review_remote=review_remote,
    )
