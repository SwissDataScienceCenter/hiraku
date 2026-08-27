from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..application.services import EventService, GoalService, LineageService, ProposalService, RunService
from ..config import Config


class ReportService:
    def __init__(
        self,
        config: Config,
        goals: GoalService,
        lineage: LineageService,
        runs: RunService,
        proposals: ProposalService | None = None,
        events: EventService | None = None,
    ):
        self.config = config
        self.goals = goals
        self.lineage = lineage
        self.runs = runs
        self.proposals = proposals
        self.events = events

    def snapshot(self, goal_id: str) -> dict[str, Any]:
        runs = self.runs.rows(goal_id)
        proposals = self.proposals.list(goal_id) if self.proposals else []
        entity_ids = {goal_id, *(run["id"] for run in runs), *(item["id"] for item in proposals)}
        events = []
        if self.events:
            events = [
                event
                for event in self.events.list()
                if event["entity_id"] in entity_ids or event["entity_id"].startswith(f"{goal_id}/")
            ]
        return {
            "projection": True,
            "warning": "Generated from .hiraku/state.db; do not edit as lifecycle state.",
            "goal": self.goals.get(goal_id).to_dict(),
            "experiments": self.lineage.forest(goal_id),
            "runs": runs,
            "proposals": proposals,
            "events": events,
        }

    def export_json(self, goal_id: str, path: Path | None = None) -> Path:
        destination = path or self.config.reports_dir / f"{goal_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.snapshot(goal_id), indent=2) + "\n", encoding="utf-8")
        return destination

    def export_csv(self, goal_id: str, path: Path | None = None) -> Path:
        destination = path or self.config.reports_dir / f"{goal_id}-runs.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = self.runs.rows(goal_id)
        fields = sorted({key for row in rows for key in row if key != "provenance"})
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in fields} for row in rows)
        return destination

    def export_markdown(self, goal_id: str, path: Path | None = None) -> Path:
        snapshot = self.snapshot(goal_id)
        goal = snapshot["goal"]
        destination = path or self.config.reports_dir / f"{goal_id}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "<!-- GENERATED PROJECTION: lifecycle state lives in .hiraku/state.db -->",
            f"# {goal['title']}",
            "",
            goal["objective"] or "_No objective recorded._",
            "",
            f"- Primary metric: `{goal['metric']}` ({goal['direction']})",
            f"- Baseline commit: `{goal['baseline_commit']}`",
            "",
            "## Experiment lineage",
            "",
        ]

        def render(nodes: list[dict[str, Any]], depth: int = 0) -> None:
            for node in nodes:
                marker = "  " * depth + "- "
                lines.append(
                    f"{marker}`{node['id']}` — {node['status']}" + (f" / {node['verdict']}" if node["verdict"] else "")
                )
                render(node["children"], depth + 1)

        render(snapshot["experiments"])
        if snapshot["proposals"]:
            lines.extend(["", "## Auto proposals", ""])
            for proposal in snapshot["proposals"]:
                lines.append(
                    f"- `{proposal['id']}` / `{proposal['experiment_id']}` — {proposal['status']}: "
                    f"{proposal['hypothesis']}"
                )
        lines.extend(["", "## Runs", ""])
        for run in snapshot["runs"]:
            lines.append(f"- `{run['id']}` — `{run['experiment_id']}` / {run['entrypoint']} / {run['status']}")
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination
