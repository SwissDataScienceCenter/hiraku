from __future__ import annotations

import hashlib
import os
import platform
import signal
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .config import Config
from .database import Database, record_event
from .errors import HirakuError
from .git import GitAdapter
from .services import ExperimentService, GoalService, RunService, split_experiment_ref


def _digest_lockfile(root: Path) -> str | None:
    for name in ("uv.lock", "poetry.lock", "pdm.lock", "requirements.txt"):
        path = root / name
        if path.is_file():
            return f"{name}:sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    return None


def _provenance(root: Path) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "lockfile": _digest_lockfile(root),
        "accelerator": os.environ.get("CUDA_VISIBLE_DEVICES") or "unspecified",
        "container": os.environ.get("HOSTNAME") if Path("/.dockerenv").exists() else None,
    }


class Launcher:
    def __init__(
        self,
        config: Config,
        db: Database,
        git: GitAdapter,
        goals: GoalService,
        experiments: ExperimentService,
        runs: RunService,
    ):
        self.config = config
        self.db = db
        self.git = git
        self.goals = goals
        self.experiments = experiments
        self.runs = runs

    def launch(self, target: str, commit_ref: str | None = None) -> tuple[str, int]:
        try:
            experiment_ref, entrypoint = target.rsplit(":", 1)
        except ValueError as exc:
            raise HirakuError("Launch target must be <goal>/<experiment>:<entrypoint>.") from exc
        goal_id, _ = split_experiment_ref(experiment_ref)
        experiment = self.experiments.get(experiment_ref)
        if experiment.status in {"concluded", "abandoned"}:
            raise HirakuError(f"Cannot launch an Experiment with status '{experiment.status}'.")
        goal = self.goals.get(goal_id)
        definition = goal.launch_manifest["entrypoints"].get(entrypoint)
        if definition is None:
            raise HirakuError(f"Entrypoint '{entrypoint}' is not defined for Goal '{goal_id}'.")

        if commit_ref:
            commit = self.git.resolve_commit(commit_ref)
            if not self.git.is_ancestor(experiment.parent_commit, commit) or not self.git.is_ancestor(
                commit, experiment.branch
            ):
                raise HirakuError(f"Commit {commit[:12]} is not a source revision on branch '{experiment.branch}'.")
            context = self.git.detached_worktree(commit)
        else:
            self.git.ensure_clean()
            branch = self.git.current_branch()
            if branch != experiment.branch:
                raise HirakuError(
                    f"Experiment '{experiment_ref}' must launch from branch '{experiment.branch}', "
                    f"but the active branch is '{branch}'."
                )
            commit = self.git.head_commit()
            context = nullcontext(self.config.root)

        if experiment.evaluated_commit and experiment.evaluated_commit != commit:
            raise HirakuError(f"Experiment is frozen at {experiment.evaluated_commit[:12]}; requested {commit[:12]}.")

        with context as execution_root:
            cwd = (execution_root / definition["cwd"]).resolve()
            if execution_root.resolve() not in (cwd, *cwd.parents):
                raise HirakuError("Entrypoint cwd escapes the execution checkout.")
            if not cwd.is_dir():
                raise HirakuError(f"Entrypoint cwd does not exist: {definition['cwd']}")

            experiment = self.experiments.freeze(experiment, commit)
            provenance = _provenance(execution_root)
            run_id = self.runs.create(experiment, commit, entrypoint, provenance)
            self.config.metrics_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = self.config.metrics_dir / f"{run_id}.json"
            metrics_path.unlink(missing_ok=True)
            env = os.environ.copy()
            env.update(definition.get("env", {}))
            env.update(
                {
                    "HIRAKU_RUN_ID": run_id,
                    "HIRAKU_GOAL_ID": goal_id,
                    "HIRAKU_EXPERIMENT_ID": experiment.id,
                    "HIRAKU_EXPERIMENT_KIND": experiment.kind,
                    "HIRAKU_GIT_COMMIT": commit,
                    "HIRAKU_METRICS_PATH": str(metrics_path),
                    "HIRAKU_PROJECT_ROOT": str(self.config.root),
                    "HIRAKU_STATE_PATH": str(self.config.state_path),
                }
            )
            process: subprocess.Popen[bytes] | None = None
            exit_code: int | None = None
            status = "failed"
            try:
                process = subprocess.Popen(definition["command"], cwd=cwd, env=env)
                exit_code = process.wait()
                status = "completed" if exit_code == 0 else ("killed" if exit_code < 0 else "failed")
            except KeyboardInterrupt:
                status = "killed"
                if process and process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        exit_code = process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        exit_code = process.wait()
            except OSError as exc:
                self.runs.finalize(run_id, "failed", None)
                raise HirakuError(f"Could not start entrypoint '{entrypoint}': {exc}") from exc
            try:
                self.runs.ingest_metrics_file(run_id, metrics_path)
            except HirakuError as exc:
                with self.db.transaction() as connection:
                    record_event(connection, "run", run_id, "metric_ingest_failed", {"error": str(exc)})
            self.runs.finalize(run_id, status, exit_code)
            return run_id, exit_code or 0
