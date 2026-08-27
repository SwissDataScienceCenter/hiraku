from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..app import HirakuApp
from ..application.services import split_experiment_ref
from ..errors import HirakuError
from .agent_protocol import AgentAdapter, AgentRequest, response_payload
from .context import collect_project_context


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
            f"A bounded alternative implementation may improve {goal.metric} relative to the immutable Goal baseline.",
        )

    def propose(
        self,
        goal_id: str,
        experiment_id: str,
        hypothesis: str,
        parent_experiment_id: str | None = None,
    ) -> dict[str, Any]:
        return self.app.proposals.create(
            goal_id,
            experiment_id,
            hypothesis,
            parent_experiment_id,
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.app.proposals.get(proposal_id)

    def list(self, goal_id: str) -> list[dict[str, Any]]:
        return self.app.proposals.list(goal_id)

    def next(self, goal_id: str) -> dict[str, Any]:
        pending = self.app.proposals.list(goal_id, status="pending")
        proposal = pending[0] if pending else self.discover(goal_id)
        reference = f"{goal_id}/{proposal['experiment_id']}"
        parent_id = proposal["parent_experiment_id"]
        if parent_id is None:
            best = self.app.analysis.best(goal_id)
            if best:
                best_experiment = self.app.experiments.get(f"{goal_id}/{best['experiment_id']}")
                if best_experiment.kind != "baseline":
                    parent_id = best_experiment.id
        parent = f"{goal_id}/{parent_id}" if parent_id else None
        try:
            experiment = self.app.experiments.create(
                reference,
                proposal["hypothesis"],
                parent=parent,
                origin="auto",
            )
        except HirakuError as exc:
            self.app.events.record("proposal", proposal["id"], "implementation_failure", {"error": str(exc)})
            raise
        self.app.proposals.admit(proposal["id"], experiment.id)
        return experiment.to_dict()

    def context(self, goal_id: str) -> dict[str, Any]:
        goal = self.app.goals.get(goal_id)
        return {
            "goal": goal.to_dict(),
            "lineage": self.app.lineage.forest(goal_id),
            "experiments": [item.to_dict() for item in self.app.experiments.list(goal_id)],
            "runs": self.app.runs.rows(goal_id),
            "proposals": self.app.proposals.list(goal_id),
        }

    def complete_implementation(self, reference: str, summary: str = "") -> dict[str, Any]:
        return self.app.experiments.finalize_agent_implementation(reference, summary).to_dict()

    def analyze(self, reference: str) -> dict[str, Any]:
        split_experiment_ref(reference)
        return self.app.analysis.conclude(reference).to_dict()

    def baseline(self, goal_id: str) -> dict[str, Any]:
        goal = self.app.goals.get(goal_id)
        entrypoints = goal.launch_manifest.get("auto", {}).get("allowed_entrypoints", [])
        if not entrypoints:
            raise HirakuError("Auto baseline requires approved 'auto.allowed_entrypoints'.")
        existing = [item for item in self.app.experiments.list(goal_id) if item.kind == "baseline"]
        if existing:
            baseline = existing[0]
            rows = self.app.runs.rows(goal_id, baseline.id)
            if rows:
                return {"experiment": baseline.to_dict(), "run": rows[-1], "created": False}
            reference = f"{goal_id}/{baseline.id}"
        else:
            reference = f"{goal_id}/auto-baseline"
            baseline = self.app.experiments.create(
                reference,
                "The immutable Goal source establishes the automatic reference.",
                kind="baseline",
                origin="auto",
            )
        run_id, _ = self.app.launcher.launch(f"{reference}:{entrypoints[0]}")
        return {
            "experiment": self.app.experiments.get(reference).to_dict(),
            "run": self.app.runs.rows(goal_id, baseline.id)[-1] | {"id": run_id},
            "created": not existing,
        }

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
            raise HirakuError("Auto loop requires an approved fixed 'auto.implement' command in the Goal manifest.")
        deadline = time.monotonic() + time_budget.total_seconds()
        created = 0
        launched = 0
        failures = 0
        baseline = [item for item in self.app.experiments.list(goal_id) if item.kind == "baseline"]
        if baseline and (not baseline[0].evaluated_commit or not self.app.runs.rows(goal_id, baseline[0].id)):
            raise HirakuError(f"Baseline Experiment '{goal_id}/{baseline[0].id}' must be evaluated before auto mode.")
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
                self.app.events.record("experiment", reference, "execution_failure", {"error": str(exc)})
        while failures == 0 and created < max_experiments and launched < max_runs and time.monotonic() < deadline:
            experiment = self.next(goal_id)
            created += 1
            reference = f"{goal_id}/{experiment['id']}"
            try:
                self.app.experiments.implement_auto(reference, implementation)
            except HirakuError as exc:
                failures += 1
                self.app.events.record("experiment", reference, "implementation_failure", {"error": str(exc)})
                break
            try:
                self.app.launcher.launch(f"{reference}:{entrypoints[0]}")
                launched += 1
            except HirakuError as exc:
                failures += 1
                self.app.events.record("experiment", reference, "execution_failure", {"error": str(exc)})
                break
            self.app.analysis.conclude(reference)
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


@dataclass
class AgentResearchService:
    """Bounded autoresearch driven through the vendor-neutral agent protocol."""

    app: HirakuApp
    adapter: AgentAdapter

    def draft_goal(
        self,
        goal_id: str,
        question: str,
        *,
        launch_path: Any | None = None,
        apply: bool = False,
        max_files: int = 80,
        max_bytes: int = 120_000,
    ) -> dict[str, Any]:
        if not question.strip():
            raise HirakuError("Goal drafting question cannot be empty.")
        context = collect_project_context(self.app.config.root, max_files=max_files, max_bytes=max_bytes)
        request = AgentRequest.create(
            "draft_goal",
            self.app.config.root,
            {"question": question, "project_context": context},
            {
                "task": "Draft a measurable research Goal from the project context.",
                "constraints": [
                    "Do not invent facts that are absent from the project context.",
                    "Use null or an empty value for unknown fields and list them in unset.",
                    "The draft is advisory until the human explicitly applies it.",
                ],
                "response_payload": {
                    "goal_id": goal_id,
                    "title": "short title",
                    "objective": "measurable objective",
                    "metric": "metric name or null",
                    "direction": "min, max, or null",
                    "target": "number or null",
                    "evaluation_protocol": "protocol or empty string",
                    "constraints": ["constraint"],
                    "unset": ["field names requiring human input"],
                    "rationale": "brief evidence-grounded rationale",
                },
            },
        )
        self.app.events.record(
            "project",
            self.app.config.root.name,
            "agent_requested",
            {"request_id": request.request_id, "action": "draft_goal"},
        )
        payload = response_payload(self.adapter.exchange(request), "draft_goal")
        draft = self._validate_goal_draft(payload, goal_id)
        result: dict[str, Any] = {"draft": draft, "applied": False}
        if apply:
            if launch_path is None:
                raise HirakuError("Applying a drafted Goal requires --launch PATH.")
            if draft["metric"] is None or draft["direction"] is None:
                raise HirakuError("Cannot apply an incomplete Goal draft; provide metric and direction first.")
            goal = self.app.goals.create(
                goal_id,
                draft["metric"],
                draft["direction"],
                launch_path,
                title=draft["title"],
                objective=draft["objective"],
                target=draft["target"],
                evaluation_protocol=draft["evaluation_protocol"],
                constraints=draft["constraints"],
            )
            result.update({"applied": True, "goal": goal.to_dict()})
        return result

    @staticmethod
    def _validate_goal_draft(payload: dict[str, Any], goal_id: str) -> dict[str, Any]:
        if payload.get("goal_id", goal_id) != goal_id:
            raise HirakuError(f"Agent Goal draft must target '{goal_id}'.")
        title = payload.get("title", goal_id.replace("-", " ").title())
        objective = payload.get("objective", "")
        metric = payload.get("metric")
        direction = payload.get("direction")
        target = payload.get("target")
        protocol = payload.get("evaluation_protocol", "")
        constraints = payload.get("constraints", [])
        unset = payload.get("unset", [])
        rationale = payload.get("rationale", "")
        if not isinstance(title, str) or not isinstance(objective, str) or not isinstance(protocol, str):
            raise HirakuError("Agent Goal draft title, objective, and evaluation_protocol must be strings.")
        if metric is not None and (not isinstance(metric, str) or not metric.strip()):
            raise HirakuError("Agent Goal draft metric must be a non-empty string or null.")
        if direction is not None and direction not in {"min", "max"}:
            raise HirakuError("Agent Goal draft direction must be 'min', 'max', or null.")
        if target is not None and not isinstance(target, (int, float)):
            raise HirakuError("Agent Goal draft target must be numeric or null.")
        if not isinstance(constraints, list) or not all(isinstance(item, str) for item in constraints):
            raise HirakuError("Agent Goal draft constraints must be a list of strings.")
        if not isinstance(unset, list) or not all(isinstance(item, str) for item in unset):
            raise HirakuError("Agent Goal draft unset must be a list of strings.")
        if not isinstance(rationale, str):
            raise HirakuError("Agent Goal draft rationale must be a string.")
        return {
            "goal_id": goal_id,
            "title": title.strip(),
            "objective": objective.strip(),
            "metric": metric.strip() if isinstance(metric, str) else None,
            "direction": direction,
            "target": target,
            "evaluation_protocol": protocol.strip(),
            "constraints": constraints,
            "unset": unset,
            "rationale": rationale.strip(),
        }

    def context(self, goal_id: str, *, focus: str | None = None) -> dict[str, Any]:
        goal = self.app.goals.get(goal_id)
        experiments = [item.to_dict() for item in self.app.experiments.list(goal_id)]
        runs = self.app.runs.rows(goal_id)
        return {
            "goal": goal.to_dict(),
            "focus_experiment": focus,
            "lineage": self.app.lineage.forest(goal_id),
            "experiments": experiments,
            "runs": runs,
            "proposals": self.app.proposals.list(goal_id),
        }

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
            raise HirakuError("Agent loop requires approved 'auto.allowed_entrypoints'.")

        deadline = time.monotonic() + time_budget.total_seconds()
        created = 0
        launched = 0
        failures = 0
        stop_reason = "agent"

        baseline = [item for item in self.app.experiments.list(goal_id) if item.kind == "baseline"]
        if baseline and (not baseline[0].evaluated_commit or not self.app.runs.rows(goal_id, baseline[0].id)):
            raise HirakuError(f"Baseline Experiment '{goal_id}/{baseline[0].id}' must be evaluated first.")
        if not baseline:
            if time.monotonic() >= deadline or created >= max_experiments or launched >= max_runs:
                return self._result(goal_id, created, launched, failures, "budget_before_baseline")
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
                self.app.events.record("experiment", reference, "execution_failure", {"error": str(exc)})
                return self._result(goal_id, created, launched, failures, "failure")

        while created < max_experiments and launched < max_runs and time.monotonic() < deadline:
            try:
                proposal = self._propose(
                    goal_id,
                    max_experiments - created,
                    max_runs - launched,
                    deadline,
                )
            except HirakuError as exc:
                failures += 1
                self.app.events.record("goal", goal_id, "agent_failure", {"phase": "propose", "error": str(exc)})
                stop_reason = "failure"
                break
            if proposal is None:
                stop_reason = "agent"
                break
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                break

            experiment_id = proposal["experiment_id"]
            hypothesis = proposal["hypothesis"]
            parent_id = proposal.get("parent_experiment_id")
            proposal_record = self.app.proposals.create(goal_id, experiment_id, hypothesis, parent_id)
            reference = f"{goal_id}/{experiment_id}"
            parent = f"{goal_id}/{parent_id}" if parent_id else None
            try:
                experiment = self.app.experiments.create(
                    reference,
                    hypothesis,
                    parent=parent,
                    origin="auto",
                )
                created += 1
                self.app.proposals.admit(proposal_record["id"], experiment.id)
                self._implement(goal_id, reference, deadline)
            except HirakuError as exc:
                failures += 1
                self.app.events.record(
                    "experiment",
                    reference,
                    "implementation_failure",
                    {"error": str(exc)},
                )
                stop_reason = "failure"
                break

            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                break

            try:
                run_id, exit_code = self.app.launcher.launch(f"{reference}:{entrypoints[0]}")
                launched += 1
            except HirakuError as exc:
                failures += 1
                self.app.events.record("experiment", reference, "execution_failure", {"error": str(exc)})
                stop_reason = "failure"
                break
            if exit_code:
                failures += 1
                self.app.events.record(
                    "experiment",
                    reference,
                    "execution_failure",
                    {"run_id": run_id, "exit_code": exit_code},
                )
                stop_reason = "failure"
                break

            try:
                should_continue = self._analyze(goal_id, experiment_id, deadline)
            except HirakuError as exc:
                failures += 1
                self.app.events.record(
                    "experiment",
                    reference,
                    "agent_failure",
                    {"phase": "analyze", "error": str(exc)},
                )
                stop_reason = "failure"
                break
            if not should_continue:
                stop_reason = "agent"
                break
        else:
            stop_reason = (
                "time_budget"
                if time.monotonic() >= deadline
                else "run_budget"
                if launched >= max_runs
                else "experiment_budget"
            )
        return self._result(goal_id, created, launched, failures, stop_reason)

    def _request(
        self,
        action: str,
        goal_id: str,
        instructions: dict[str, Any],
        *,
        focus: str | None = None,
    ) -> dict[str, Any]:
        request = AgentRequest.create(
            action,
            self.app.config.root,
            self.context(goal_id, focus=focus),
            instructions,
        )
        self.app.events.record(
            "goal" if focus is None else "experiment",
            goal_id if focus is None else f"{goal_id}/{focus}",
            "agent_requested",
            {"request_id": request.request_id, "action": action},
        )
        response = self.adapter.exchange(request)
        self.app.events.record(
            "goal" if focus is None else "experiment",
            goal_id if focus is None else f"{goal_id}/{focus}",
            "agent_responded",
            {"request_id": request.request_id, "action": action},
        )
        return response_payload(response, action)

    def _propose(
        self,
        goal_id: str,
        remaining_experiments: int,
        remaining_runs: int,
        deadline: float,
    ) -> dict[str, Any] | None:
        payload = self._request(
            "propose",
            goal_id,
            {
                "task": "Propose one falsifiable, bounded implementation experiment or stop.",
                "constraints": [
                    "Do not change the Goal, evaluation protocol, manifest, policy, or budgets.",
                    "Use a new lowercase experiment_id.",
                    "A parent may only be an evaluated Experiment from this Goal.",
                ],
                "remaining": {
                    "experiments": remaining_experiments,
                    "runs": remaining_runs,
                    "seconds": max(0, int(deadline - time.monotonic())),
                },
                "response_payload": {
                    "decision": "propose | stop",
                    "experiment_id": "required when proposing",
                    "hypothesis": "required falsifiable claim when proposing",
                    "parent_experiment_id": "optional",
                    "reason": "required when stopping",
                },
            },
        )
        decision = payload.get("decision")
        if decision == "stop":
            return None
        if decision != "propose":
            raise HirakuError("Agent proposal decision must be 'propose' or 'stop'.")
        experiment_id = payload.get("experiment_id")
        hypothesis = payload.get("hypothesis")
        parent = payload.get("parent_experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise HirakuError("Agent proposal is missing experiment_id.")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise HirakuError("Agent proposal is missing a hypothesis.")
        if parent is not None and not isinstance(parent, str):
            raise HirakuError("Agent proposal parent_experiment_id must be a string or null.")
        return {
            "experiment_id": experiment_id,
            "hypothesis": hypothesis.strip(),
            "parent_experiment_id": parent,
        }

    def _implement(self, goal_id: str, reference: str, deadline: float) -> None:
        _, experiment_id = split_experiment_ref(reference)
        payload = self._request(
            "implement",
            goal_id,
            {
                "task": "Implement the focused Experiment in the active Git branch.",
                "constraints": [
                    "Edit only project source required by the hypothesis.",
                    "Do not edit Hiraku lifecycle state, Goal configuration, manifests, or budgets.",
                    "Do not commit; Hiraku validates and commits the implementation.",
                    "Return only after the implementation is ready for evaluation.",
                ],
                "deadline_seconds": max(0, int(deadline - time.monotonic())),
                "response_payload": {"status": "implemented", "summary": "short change summary"},
            },
            focus=experiment_id,
        )
        if payload.get("status") != "implemented":
            raise HirakuError("Agent implementation status must be 'implemented'.")
        summary = payload.get("summary", "")
        if not isinstance(summary, str):
            raise HirakuError("Agent implementation summary must be a string.")
        self.app.experiments.finalize_agent_implementation(reference, summary)

    def _analyze(self, goal_id: str, experiment_id: str, deadline: float) -> bool:
        verdict = self.app.analysis.verdict(f"{goal_id}/{experiment_id}")
        payload = self._request(
            "analyze",
            goal_id,
            {
                "task": "Explain the evidence and decide whether another bounded experiment is useful.",
                "computed_verdict": verdict,
                "constraints": [
                    "Use only recorded Runs and the immutable baseline.",
                    "The verdict is computed by Hiraku and cannot be changed.",
                    "Do not claim statistical significance unless the Goal protocol defines it.",
                ],
                "deadline_seconds": max(0, int(deadline - time.monotonic())),
                "response_payload": {
                    "verdict": verdict,
                    "rationale": "evidence-grounded analysis",
                    "continue": "boolean",
                },
            },
            focus=experiment_id,
        )
        if payload.get("verdict") != verdict:
            raise HirakuError(f"Agent analysis verdict must match computed verdict '{verdict}'.")
        rationale = payload.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise HirakuError("Agent analysis must include a rationale.")
        should_continue = payload.get("continue")
        if not isinstance(should_continue, bool):
            raise HirakuError("Agent analysis 'continue' must be boolean.")
        reference = f"{goal_id}/{experiment_id}"
        self.app.analysis.conclude(reference)
        self.app.events.record(
            "experiment",
            reference,
            "agent_analysis",
            {"verdict": verdict, "rationale": rationale, "continue": should_continue},
        )
        return should_continue

    @staticmethod
    def _result(
        goal_id: str,
        created: int,
        launched: int,
        failures: int,
        stop_reason: str,
    ) -> dict[str, Any]:
        return {
            "goal_id": goal_id,
            "experiments_created": created,
            "runs_launched": launched,
            "failures": failures,
            "stop_reason": stop_reason,
            "driver": "agent-protocol",
        }
