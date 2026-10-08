"""MCP tools for committing, launching, and analyzing experiments."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ...core.config import HirakuConfig
from ...core.errors import ValidationError
from ...core.git import GitRepository
from ...core.launch_job import get_launch_status as read_launch_status
from ...core.launch_job import submit_launch
from ...core.record import load_record
from ..schemas import Analysis


def register(
    server: MCPServer, get_config: Callable[[], HirakuConfig]
) -> dict[str, Callable[..., Any]]:
    """Register experiment implementation and evidence tools."""

    @server.tool()
    def commit_implementation(
        record_id: str, paths: list[str], message: str
    ) -> dict[str, Any]:
        """Validate and commit explicitly selected implementation files."""
        config = get_config()
        record = load_record(record_id, config=config)
        if record.type != "experiment":
            raise ValidationError("Implementation requires an experiment record")
        if not paths or not message.strip():
            raise ValidationError("paths and message must be non-empty")
        git = GitRepository(config)
        resolved_paths: list[str] = []
        for path in paths:
            candidate = (config.root / path).resolve()
            try:
                candidate.relative_to(config.root.resolve())
            except ValueError as exc:
                raise ValidationError(f"Path is outside the project: {path}") from exc
            if (
                candidate == config.hiraku_dir
                or config.hiraku_dir in candidate.parents
                or ".git" in candidate.parts
            ):
                raise ValidationError(
                    "Hiraku state and Git metadata cannot be implementation paths"
                )
            if not candidate.is_file():
                raise ValidationError(f"Implementation file does not exist: {path}")
            resolved_paths.append(str(candidate.relative_to(config.root.resolve())))
        staged_paths = {
            path
            for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split(
                "\0"
            )
            if path
        }
        unrelated_staged = staged_paths.difference(resolved_paths)
        if unrelated_staged:
            raise ValidationError(
                f"Refusing to commit unrelated staged paths: {sorted(unrelated_staged)}"
            )
        git._git("add", "--", *resolved_paths)
        git._git("diff", "--cached", "--check")
        changed_paths = {
            path
            for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split(
                "\0"
            )
            if path
        }
        if not changed_paths:
            untracked_paths = [
                path
                for path in resolved_paths
                if git._git("ls-files", "--error-unmatch", path, check=False).returncode
                != 0
            ]
            if untracked_paths:
                raise ValidationError(
                    "No implementation changes were staged and these paths are not "
                    f"tracked: {untracked_paths}"
                )
            record.commit = git.head
            record.status = "implemented"
            record.save()
            return {
                "record_id": record.id,
                "commit": record.commit,
                "committed_paths": [],
                "bound_paths": resolved_paths,
            }
        git._git("-c", "commit.gpgSign=false", "commit", "-m", message)
        commit = git.head
        record.commit = commit
        record.status = "implemented"
        record.save()
        return {
            "record_id": record.id,
            "commit": commit,
            "committed_paths": resolved_paths,
        }

    @server.tool()
    def launch_experiment(
        record_id: str,
        entrypoint: str,
        args: dict[str, Any] | None = None,
        cpu_time_limit_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Submit a committed experiment and return its launch handle."""
        config = get_config()
        record = load_record(record_id, config=config)
        if record.type != "experiment" or not record.commit:
            raise ValidationError("Launch requires an experiment with a bound commit")
        git = GitRepository(config)
        commit = git.resolve(record.commit)
        if commit != record.commit:
            raise ValidationError("Experiment commit is invalid")
        record.entrypoint(entrypoint, config=config)
        return submit_launch(
            config,
            record_id=record.id,
            entrypoint=entrypoint,
            commit=commit,
            args=args or {},
            cpu_time_limit_seconds=cpu_time_limit_seconds,
        )

    @server.tool()
    def get_launch_status(launch_id: str) -> dict[str, Any]:
        """Return execution state and recent output for a submitted launch."""
        return read_launch_status(get_config(), launch_id)

    @server.tool()
    def record_analysis(record_id: str, analysis: Analysis) -> dict[str, Any]:
        """Validate cited runs and persist the experiment conclusion."""
        config = get_config()
        record = load_record(record_id, config=config)
        if record.type != "experiment" or not record.commit:
            raise ValidationError("Analysis requires a committed experiment")
        known = {run.id: run for run in record.runs(config=config)}
        cited = []
        for run_id in analysis.run_ids:
            run = known.get(run_id)
            if run is None or run.record_id != record.id or run.commit != record.commit:
                raise ValidationError(
                    f"Run {run_id!r} is not attributed to this experiment commit"
                )
            cited.append(run_id)
        record.conclusion = analysis.conclusion
        record.decision = analysis.decision
        record.analysis_run_ids = cited
        record.status = "analyzed"
        record.save()
        return {
            "record_id": record.id,
            "status": record.status,
            "run_ids": cited,
            "conclusion": record.conclusion,
            "decision": record.decision,
        }

    return {
        "commit_implementation": commit_implementation,
        "launch_experiment": launch_experiment,
        "get_launch_status": get_launch_status,
        "record_analysis": record_analysis,
    }
