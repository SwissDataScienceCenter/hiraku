"""MCP tools for committing, launching, and analyzing experiments."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ...core.config import HirakuConfig
from ...core.errors import ValidationError
from ...core.experiments import (
    commit_implementation as commit_experiment_implementation,
)
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
        return commit_experiment_implementation(
            record_id, paths, message, config=get_config()
        )

    @server.tool()
    def launch_experiment(
        record_id: str,
        entrypoint: str,
        args: dict[str, Any] | None = None,
        cpu_time_limit_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Submit a committed experiment and return its launch handle.

        ``cpu_time_limit_seconds`` applies per process on POSIX systems; it does
        not cap aggregate CPU usage or elapsed wall-clock time for the job.
        """
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
