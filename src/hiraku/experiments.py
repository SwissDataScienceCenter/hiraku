from __future__ import annotations

import shutil
from typing import Literal

import yaml

from hiraku.config import ProjectConfig
from hiraku.errors import HirakuError
from hiraku.git import (
    current_branch,
    delete_branch,
    ensure_clean,
    local_branch_exists,
    switch_branch,
    switch_new_branch,
    validate_branch_name,
)
from hiraku.records import Experiment, load_experiment, validate_experiment_id


def create_experiment(
    config: ProjectConfig,
    experiment_id: str,
    *,
    mode: Literal["guided", "auto"] = "guided",
    branch: str | None = None,
) -> Experiment:
    validate_experiment_id(experiment_id)
    if mode not in {"guided", "auto"}:
        raise HirakuError("Experiment mode must be 'guided' or 'auto'.")
    selected_branch = branch or f"exp/{experiment_id}"
    validate_branch_name(selected_branch, config.root)
    ensure_clean(config.root)
    original_branch = current_branch(config.root)
    if local_branch_exists(selected_branch, config.root):
        raise HirakuError(f"Local branch '{selected_branch}' already exists.")

    experiment_dir = config.experiments_dir / experiment_id
    if experiment_dir.exists() or experiment_dir.is_symlink():
        raise HirakuError(f"Experiment directory already exists: {experiment_dir}")
    for source in (config.experiment_template, config.policies[mode]):
        if not source.is_file():
            raise HirakuError(f"Required project template does not exist: {source}")
    try:
        template = config.experiment_template.read_text(encoding="utf-8")
        policy = config.policies[mode].read_text(encoding="utf-8")
    except OSError as exc:
        raise HirakuError(f"Could not read project template: {exc}") from exc

    metadata = {"id": experiment_id, "branch": selected_branch, "mode": mode}
    front_matter = yaml.safe_dump(metadata, sort_keys=False).rstrip()
    record = f"---\n{front_matter}\n---\n\n{template}"
    if not record.endswith("\n"):
        record += "\n"

    created_branch = False
    created_dir = False
    try:
        switch_new_branch(selected_branch, config.root)
        created_branch = True
        experiment_dir.mkdir(parents=True, exist_ok=False)
        created_dir = True
        (experiment_dir / "experiment.md").write_text(record, encoding="utf-8")
        (experiment_dir / "policy.md").write_text(policy, encoding="utf-8")
        (experiment_dir / "launch.yml").write_text("entrypoints: {}\n", encoding="utf-8")
    except Exception as exc:
        rollback_errors: list[str] = []
        if created_dir:
            try:
                shutil.rmtree(experiment_dir)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if created_branch:
            try:
                switch_branch(original_branch, config.root)
                delete_branch(selected_branch, config.root)
            except HirakuError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        detail = (
            f" Experiment creation rollback also failed: {'; '.join(rollback_errors)}"
            if rollback_errors
            else ""
        )
        if isinstance(exc, HirakuError):
            raise HirakuError(f"{exc}{detail}") from exc
        raise HirakuError(f"Could not create experiment: {exc}.{detail}") from exc
    return load_experiment(config, experiment_id)
