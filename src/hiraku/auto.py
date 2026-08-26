from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .app import HirakuApp
from .errors import HirakuError


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smh])", value)
    if not match:
        raise HirakuError("Time budget must look like 30m, 2h, or 90s.")
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    return timedelta(seconds=amount * multiplier)


@dataclass
class AutoService:
    app: HirakuApp

    def discover(self, goal_id: str) -> dict[str, Any]:
        goal = self.app.goals.get(goal_id)
        number = len(self.app.proposals.list(goal_id)) + 1
        return self.app.proposals.create(
            goal_id,
            f"auto-{number:03d}",
            f"A bounded alternative implementation may improve {goal.metric} relative to "
            "the immutable Goal baseline.",
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.app.proposals.get(proposal_id)

    def list(self, goal_id: str) -> list[dict[str, Any]]:
        return self.app.proposals.list(goal_id)

    def next(self, goal_id: str) -> dict[str, Any]:
        pending = self.app.proposals.list(goal_id, status="pending")
        proposal = pending[0] if pending else self.discover(goal_id)
        reference = f"{goal_id}/{proposal['experiment_id']}"
        parent = (
            f"{goal_id}/{proposal['parent_experiment_id']}"
            if proposal["parent_experiment_id"]
            else None
        )
        try:
            experiment = self.app.experiments.create(
                reference,
                proposal["hypothesis"],
                parent=parent,
                origin="auto",
            )
        except HirakuError as exc:
            self.app.events.record(
                "proposal", proposal["id"], "implementation_failure", {"error": str(exc)}
            )
            raise
        self.app.proposals.admit(proposal["id"], experiment.id)
        return experiment.to_dict()

    def loop(
        self,
        goal_id: str,
        *,
        max_experiments: int,
        max_runs: int,
        time_budget: timedelta,
    ) -> dict[str, Any]:
        if max_experiments < 1 or max_runs < 1:
            raise HirakuError("Auto limits must be positive.")
        goal = self.app.goals.get(goal_id)
        entrypoints = goal.launch_manifest.get("auto", {}).get("allowed_entrypoints", [])
        if not entrypoints:
            raise HirakuError("Auto mode requires approved 'auto.allowed_entrypoints'.")
        implementation = goal.launch_manifest.get("auto", {}).get("implement")
        if not implementation:
            raise HirakuError(
                "Auto loop requires an approved fixed 'auto.implement' command in the Goal manifest."
            )
        deadline = time.monotonic() + time_budget.total_seconds()
        created = 0
        launched = 0
        failures = 0
        baseline = [item for item in self.app.experiments.list(goal_id) if item.kind == "baseline"]
        if baseline and (
            not baseline[0].evaluated_commit or not self.app.runs.rows(goal_id, baseline[0].id)
        ):
            raise HirakuError(
                f"Baseline Experiment '{goal_id}/{baseline[0].id}' must be evaluated before auto mode."
            )
        if not baseline and time.monotonic() < deadline:
            reference = f"{goal_id}/auto-baseline"
            try:
                self.app.experiments.create(
                    reference,
                    "The immutable Goal source establishes the automatic reference.",
                    kind="baseline",
                    origin="auto",
                )
                created += 1
                self.app.launcher.launch(f"{reference}:{entrypoints[0]}")
                launched += 1
            except HirakuError as exc:
                failures += 1
                self.app.events.record(
                    "experiment", reference, "execution_failure", {"error": str(exc)}
                )
        while (
            failures == 0
            and created < max_experiments
            and launched < max_runs
            and time.monotonic() < deadline
        ):
            experiment = self.next(goal_id)
            created += 1
            reference = f"{goal_id}/{experiment['id']}"
            try:
                self.app.experiments.implement_auto(reference, implementation)
            except HirakuError as exc:
                failures += 1
                self.app.events.record(
                    "experiment", reference, "implementation_failure", {"error": str(exc)}
                )
                break
            try:
                self.app.launcher.launch(f"{reference}:{entrypoints[0]}")
                launched += 1
            except HirakuError as exc:
                failures += 1
                self.app.events.record(
                    "experiment", reference, "execution_failure", {"error": str(exc)}
                )
                break
            self._analyze(goal_id, experiment["id"], goal.metric, goal.direction)
        stop_reason = (
            "time_budget"
            if time.monotonic() >= deadline
            else "run_budget"
            if launched >= max_runs
            else "experiment_budget"
            if created >= max_experiments
            else "failure"
        )
        return {
            "goal_id": goal_id,
            "experiments_created": created,
            "runs_launched": launched,
            "failures": failures,
            "stop_reason": stop_reason,
        }

    def _analyze(self, goal_id: str, experiment_id: str, metric: str, direction: str) -> None:
        candidate_rows = self.app.runs.rows(goal_id, experiment_id)
        baseline_ids = [
            item.id for item in self.app.experiments.list(goal_id) if item.kind == "baseline"
        ]

        def numeric(rows: list[dict[str, Any]]) -> list[float]:
            values: list[float] = []
            for row in rows:
                try:
                    values.append(float(row[metric]))
                except (KeyError, TypeError, ValueError):
                    continue
            return values

        candidate = numeric(candidate_rows)
        baseline = numeric(
            [row for baseline_id in baseline_ids for row in self.app.runs.rows(goal_id, baseline_id)]
        )
        if not candidate or not baseline:
            verdict = "inconclusive"
        elif direction == "max":
            verdict = "supports" if max(candidate) > max(baseline) else "refutes"
        else:
            verdict = "supports" if min(candidate) < min(baseline) else "refutes"
        reference = f"{goal_id}/{experiment_id}"
        self.app.experiments.conclude(reference, verdict)
        if verdict != "supports":
            self.app.events.record(
                "experiment",
                reference,
                "research_result_failure",
                {"verdict": verdict, "metric": metric},
            )
