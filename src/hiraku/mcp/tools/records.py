"""MCP tools for creating tasks and experiment records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ...core.config import HirakuConfig
from ...core.errors import ValidationError
from ...core.record import Record, list_records, load_record
from ..schemas import ExperimentProposal, ResearchTask


def register(
    server: MCPServer, get_config: Callable[[], HirakuConfig]
) -> dict[str, Callable[..., Any]]:
    """Register task, run-query, and experiment-proposal tools."""

    @server.tool()
    def create_task(task: ResearchTask) -> dict[str, Any]:
        """Create a root task with its goal, metrics, protocol, and metadata."""
        config = get_config()
        if any(record.id == task.id for record in list_records(config=config)):
            raise ValidationError(f"Record {task.id!r} already exists")
        task_fields: dict[str, Any] = {
            "type": "task",
            "goal": task.goal,
            "metrics": [metric.model_dump() for metric in task.metrics],
        }
        if task.tracking_backend is not None:
            task_fields["tracking_backend"] = task.tracking_backend
        record = Record.create(
            task.id,
            markdown=f"## Evaluation protocol\n\n{task.protocol}",
            activate=False,
            config=config,
            **task_fields,
        )
        return {"id": record.id, "type": record.type, "path": str(record.path)}

    @server.tool()
    def list_runs(record_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List normalized runs visible from a record."""
        if limit < 0:
            raise ValidationError("limit must be non-negative")
        config = get_config()
        runs = load_record(record_id, config=config).runs(config=config, limit=limit)
        return [
            {
                "id": run.id,
                "record_id": run.record_id,
                "commit": run.commit,
                "entrypoint": run.entrypoint,
                "metrics": dict(run.metrics),
                "params": dict(run.params),
                "timestamp": run.timestamp,
                "url": run.url,
            }
            for run in runs
        ]

    @server.tool()
    def propose_experiment(proposal: ExperimentProposal) -> dict[str, Any]:
        """Create a proposed child experiment record."""
        config = get_config()
        parent = load_record(proposal.parent_id, config=config)
        if any(item.id == proposal.id for item in list_records(config=config)):
            raise ValidationError(f"Record {proposal.id!r} already exists")
        values: dict[str, Any] = {
            "type": "experiment",
            "status": "proposed",
            "hypothesis": proposal.hypothesis,
            "proposal_summary": proposal.summary,
        }
        if proposal.entrypoint:
            values["entrypoints"] = {
                proposal.entrypoint.name: {
                    "command": proposal.entrypoint.command,
                    "args": proposal.entrypoint.args,
                }
            }
        record = Record.create(
            proposal.id,
            parent_id=parent.id,
            markdown=(
                f"{proposal.summary}\n\n"
                f"## Hypothesis\n\n{proposal.hypothesis}\n\n"
                f"## Implementation plan\n\n{proposal.implementation_plan}\n\n"
                f"## Evaluation plan\n\n{proposal.evaluation_plan}"
            ),
            activate=False,
            config=config,
            **values,
        )
        return {
            "id": record.id,
            "status": record.status,
        }

    return {
        "create_task": create_task,
        "list_runs": list_runs,
        "propose_experiment": propose_experiment,
    }
