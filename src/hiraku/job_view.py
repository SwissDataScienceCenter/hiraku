"""Shared CLI/MCP presentation of job observations."""

from typing import Any

from .errors import ExecutionError
from .jobs import Job


def job_view(job: Job, *, cached: bool = False) -> dict[str, Any]:
    """Observe a job and describe query failure without changing its saved status."""
    error = None
    if not cached:
        try:
            job.update()
        except ExecutionError as exc:
            error = str(exc)
    return {
        **job.model_dump(mode="json"),
        "state": "UNKNOWN" if error else job.status.state.name,
        "observation_error": error,
    }
