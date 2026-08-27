from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..database import Database, record_event, utc_now
from ..errors import HirakuError
from ..infrastructure.sqlite.repositories import GoalRepository, RunRepository
from ..manifest import load_manifest
from ..models import Experiment, Goal
from .ports import SourceControl, StateStore

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def validate_id(value: str, label: str) -> None:
    if not ID_PATTERN.fullmatch(value):
        raise HirakuError(f"Invalid {label} '{value}'. Use lowercase letters, numbers, '.', '_' or '-'.")


def split_experiment_ref(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise HirakuError("Experiment must be addressed as <goal>/<experiment>.")
    return parts[0], parts[1]


class GoalService:
    def __init__(self, db: StateStore, git: SourceControl):
        self.db = db
        self.git = git
        self.repository = GoalRepository(db)

    def create(
        self,
        goal_id: str,
        metric: str,
        direction: str,
        launch_path: Path,
        *,
        title: str | None = None,
        objective: str = "",
        target: float | None = None,
        evaluation_protocol: str = "",
        constraints: Iterable[str] = (),
    ) -> Goal:
        validate_id(goal_id, "Goal ID")
        if direction not in {"min", "max"}:
            raise HirakuError("Direction must be 'min' or 'max'.")
        if not metric.strip():
            raise HirakuError("Metric cannot be empty.")
        self.git.ensure_repository()
        self.git.ensure_clean()
        baseline = self.git.head_commit()
        manifest = load_manifest(launch_path.resolve())
        now = utc_now()
        try:
            with self.db.transaction() as connection:
                self.repository.insert(
                    connection,
                    (
                        goal_id,
                        title or goal_id.replace("-", " ").title(),
                        objective,
                        metric,
                        direction,
                        target,
                        evaluation_protocol,
                        json.dumps(list(constraints)),
                        baseline,
                        json.dumps(manifest, sort_keys=True),
                        now,
                        now,
                    ),
                )
                record_event(connection, "goal", goal_id, "created", {"baseline_commit": baseline})
        except sqlite3.IntegrityError as exc:
            raise HirakuError(f"Goal '{goal_id}' already exists.") from exc
        return self.get(goal_id)

    def get(self, goal_id: str) -> Goal:
        row = self.repository.get(goal_id)
        if row is None:
            raise HirakuError(f"Goal '{goal_id}' does not exist.")
        return Goal.from_row(row)

    def list(self) -> list[Goal]:
        rows = self.repository.list()
        return [Goal.from_row(row) for row in rows]

    def update(
        self,
        goal_id: str,
        *,
        title: str | None = None,
        objective: str | None = None,
        evaluation_protocol: str | None = None,
        constraints: list[str] | None = None,
        launch_path: Path | None = None,
        metric: str | None = None,
        direction: str | None = None,
    ) -> Goal:
        goal = self.get(goal_id)
        with self.db.transaction() as connection:
            run_count = self.repository.run_count(connection, goal_id)
            locked_change = any(
                value is not None for value in (metric, direction, evaluation_protocol, constraints, launch_path)
            )
            if run_count and locked_change:
                raise HirakuError(
                    "Metric, direction, protocol, constraints, and launch permissions cannot change after Runs exist."
                )
            manifest = load_manifest(launch_path.resolve()) if launch_path else goal.launch_manifest
            self.repository.update(
                connection,
                (
                    title if title is not None else goal.title,
                    objective if objective is not None else goal.objective,
                    metric if metric is not None else goal.metric,
                    direction if direction is not None else goal.direction,
                    evaluation_protocol if evaluation_protocol is not None else goal.evaluation_protocol,
                    json.dumps(constraints if constraints is not None else goal.constraints),
                    json.dumps(manifest, sort_keys=True),
                    utc_now(),
                ),
                goal_id,
            )
            record_event(connection, "goal", goal_id, "updated")
        return self.get(goal_id)


class ExperimentService:
    def __init__(self, db: StateStore, git: SourceControl, goals: GoalService):
        self.db = db
        self.git = git
        self.goals = goals

    def create(
        self,
        reference: str,
        hypothesis: str,
        *,
        kind: str = "hypothesis",
        parent: str | None = None,
        origin: str = "guided",
    ) -> Experiment:
        goal_id, experiment_id = split_experiment_ref(reference)
        validate_id(goal_id, "Goal ID")
        validate_id(experiment_id, "Experiment ID")
        if kind not in {"baseline", "hypothesis"}:
            raise HirakuError("Experiment kind must be 'baseline' or 'hypothesis'.")
        if origin not in {"guided", "auto"}:
            raise HirakuError("Experiment origin must be 'guided' or 'auto'.")
        if not hypothesis.strip():
            raise HirakuError("Hypothesis or expectation cannot be empty.")
        goal = self.goals.get(goal_id)
        parent_id: str | None = None
        start_commit = goal.baseline_commit
        if parent:
            parent_goal, parent_id = split_experiment_ref(parent)
            if parent_goal != goal_id:
                raise HirakuError("A child Experiment must belong to the same Goal as its parent.")
            parent_experiment = self.get(parent)
            if not parent_experiment.evaluated_commit:
                raise HirakuError("A child Experiment requires a parent with a frozen evaluated commit.")
            start_commit = parent_experiment.evaluated_commit
        if kind == "baseline" and parent_id:
            raise HirakuError("A baseline Experiment cannot have a parent.")
        branch = f"hiraku/{goal_id}/{experiment_id}"
        original = self.git.create_and_checkout_branch(branch, start_commit)
        now = utc_now()
        try:
            with self.db.transaction() as connection:
                if kind == "baseline":
                    existing = connection.execute(
                        "SELECT id FROM experiments WHERE goal_id=? AND kind='baseline'",
                        (goal_id,),
                    ).fetchone()
                    if existing:
                        raise HirakuError(f"Goal '{goal_id}' already has a baseline Experiment.")
                connection.execute(
                    "INSERT INTO experiments(id,goal_id,parent_id,kind,hypothesis,branch,parent_commit,"
                    "evaluated_commit,status,verdict,outcome,origin,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,NULL,'implementing',NULL,NULL,?,?,?)",
                    (
                        experiment_id,
                        goal_id,
                        parent_id,
                        kind,
                        hypothesis,
                        branch,
                        start_commit,
                        origin,
                        now,
                        now,
                    ),
                )
                record_event(
                    connection,
                    "experiment",
                    reference,
                    "created",
                    {"branch": branch, "parent_commit": start_commit, "origin": origin},
                )
        except Exception as exc:
            self.git.rollback_created_branch(original, branch)
            if isinstance(exc, HirakuError):
                raise
            if isinstance(exc, sqlite3.IntegrityError):
                raise HirakuError(f"Experiment '{reference}' already exists.") from exc
            raise
        return self.get(reference)

    def get(self, reference: str) -> Experiment:
        goal_id, experiment_id = split_experiment_ref(reference)
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE goal_id=? AND id=?", (goal_id, experiment_id)
            ).fetchone()
        if row is None:
            raise HirakuError(f"Experiment '{reference}' does not exist.")
        return Experiment.from_row(row)

    def list(self, goal_id: str | None = None) -> list[Experiment]:
        with self.db.connect() as connection:
            if goal_id:
                rows = connection.execute(
                    "SELECT * FROM experiments WHERE goal_id=? ORDER BY created_at,id", (goal_id,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM experiments ORDER BY goal_id,created_at,id").fetchall()
        return [Experiment.from_row(row) for row in rows]

    def freeze(self, experiment: Experiment, commit: str) -> Experiment:
        if experiment.evaluated_commit and experiment.evaluated_commit != commit:
            raise HirakuError(
                f"Experiment is frozen at {experiment.evaluated_commit[:12]}; create a child or sibling "
                "Experiment for later source changes."
            )
        if not experiment.evaluated_commit:
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE experiments SET evaluated_commit=?, status='evaluated', updated_at=? "
                    "WHERE goal_id=? AND id=? AND evaluated_commit IS NULL",
                    (commit, utc_now(), experiment.goal_id, experiment.id),
                )
                record_event(
                    connection,
                    "experiment",
                    f"{experiment.goal_id}/{experiment.id}",
                    "frozen",
                    {"commit": commit},
                )
        return self.get(f"{experiment.goal_id}/{experiment.id}")

    def conclude(self, reference: str, verdict: str, outcome: str | None = None) -> Experiment:
        if verdict not in {"supports", "refutes", "inconclusive"}:
            raise HirakuError("Verdict must be supports, refutes, or inconclusive.")
        experiment = self.get(reference)
        if experiment.status == "abandoned":
            raise HirakuError("An abandoned Experiment cannot be concluded.")
        if not experiment.evaluated_commit:
            raise HirakuError("An Experiment must be evaluated before it can be concluded.")
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE experiments SET status='concluded',verdict=?,outcome=?,updated_at=? WHERE goal_id=? AND id=?",
                (verdict, outcome, utc_now(), experiment.goal_id, experiment.id),
            )
            record_event(connection, "experiment", reference, "concluded", {"verdict": verdict, "outcome": outcome})
        return self.get(reference)

    def abandon(self, reference: str) -> Experiment:
        experiment = self.get(reference)
        if experiment.status == "concluded":
            raise HirakuError("A concluded Experiment cannot be abandoned.")
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE experiments SET status='abandoned',updated_at=? WHERE goal_id=? AND id=?",
                (utc_now(), experiment.goal_id, experiment.id),
            )
            record_event(connection, "experiment", reference, "abandoned")
        return self.get(reference)

    def implement_auto(self, reference: str, definition: dict[str, Any]) -> Experiment:
        """Run a policy-admitted fixed implementation command and commit its changes."""
        experiment = self.get(reference)
        if experiment.origin != "auto" or experiment.status != "implementing":
            raise HirakuError("Only an implementing auto-origin Experiment can be auto-implemented.")
        self.git.ensure_clean()
        if self.git.current_branch() != experiment.branch:
            raise HirakuError(f"Auto implementation requires active branch '{experiment.branch}'.")
        cwd = (self.git.root / definition.get("cwd", ".")).resolve()
        if self.git.root.resolve() not in (cwd, *cwd.parents) or not cwd.is_dir():
            raise HirakuError("Auto implementation cwd is invalid.")
        env = os.environ.copy()
        env.update(definition.get("env", {}))
        env.update(
            {
                "HIRAKU_GOAL_ID": experiment.goal_id,
                "HIRAKU_EXPERIMENT_ID": experiment.id,
                "HIRAKU_HYPOTHESIS": experiment.hypothesis,
            }
        )
        try:
            result = subprocess.run(definition["command"], cwd=cwd, env=env, check=False)
        except OSError as exc:
            raise HirakuError(f"Could not start auto implementation: {exc}") from exc
        if result.returncode:
            raise HirakuError(f"Auto implementation exited with status {result.returncode}.")
        commit = self.git.commit_all(f"hiraku: implement {reference} (auto)")
        with self.db.transaction() as connection:
            record_event(
                connection,
                "experiment",
                reference,
                "implemented",
                {"commit": commit, "origin": "auto"},
            )
        return self.get(reference)

    def finalize_agent_implementation(self, reference: str, summary: str = "") -> Experiment:
        """Commit source changes made by an admitted agent through the shared Git service."""
        experiment = self.get(reference)
        if experiment.origin != "auto" or experiment.status != "implementing":
            raise HirakuError("Only an implementing auto-origin Experiment can be finalized.")
        if self.git.current_branch() != experiment.branch:
            raise HirakuError(f"Agent implementation requires active branch '{experiment.branch}'.")
        protected = [
            path
            for path in self.git.changed_paths()
            if path == ".hiraku.yml" or path.startswith((".hiraku/", ".agents/"))
        ]
        if protected:
            raise HirakuError(
                "Agent implementation changed protected Hiraku control files: " + ", ".join(sorted(protected))
            )
        commit = self.git.commit_all(f"hiraku: implement {reference} (auto)")
        with self.db.transaction() as connection:
            record_event(
                connection,
                "experiment",
                reference,
                "implemented",
                {"commit": commit, "origin": "auto", "summary": summary},
            )
        return self.get(reference)


class RunService:
    def __init__(self, db: StateStore):
        self.db = db
        self.repository = RunRepository(db)

    def create(
        self,
        experiment: Experiment,
        commit: str,
        entrypoint: str,
        provenance: dict[str, Any],
    ) -> str:
        with self.db.transaction() as connection:
            run_id = f"R-{uuid.uuid4().hex[:12]}"
            self.repository.insert(
                connection,
                (
                    run_id,
                    experiment.goal_id,
                    experiment.id,
                    commit,
                    entrypoint,
                    json.dumps(provenance, sort_keys=True),
                    utc_now(),
                ),
            )
            record_event(connection, "run", run_id, "started", {"commit": commit})
        return run_id

    def finalize(self, run_id: str, status: str, exit_code: int | None) -> None:
        if status not in {"completed", "failed", "killed"}:
            raise HirakuError(f"Invalid terminal Run status: {status}")
        with self.db.transaction() as connection:
            row = self.repository.status(connection, run_id)
            if row is None:
                raise HirakuError(f"Run '{run_id}' does not exist.")
            if row["status"] != "running":
                raise HirakuError(f"Run '{run_id}' is already finalized.")
            self.repository.finalize(connection, run_id, status, exit_code)
            record_event(connection, "run", run_id, status, {"exit_code": exit_code})

    def log(self, run_id: str, parameters: dict[str, str], metrics: dict[str, str]) -> None:
        with self.db.transaction() as connection:
            run = self.repository.status(connection, run_id)
            if run is None:
                raise HirakuError(f"Run '{run_id}' does not exist.")
            if run["status"] != "running":
                raise HirakuError("Parameters and metrics can only be logged while a Run is active.")
            for key, value in parameters.items():
                existing = self.repository.parameter(connection, run_id, key)
                if existing and existing["value"] != value:
                    raise HirakuError(f"Run parameter '{key}' is immutable (already '{existing['value']}').")
                self.repository.insert_parameter(connection, run_id, key, value)
            for key, value in metrics.items():
                self.repository.upsert_metric(connection, run_id, key, value)
            if parameters or metrics:
                record_event(
                    connection,
                    "run",
                    run_id,
                    "logged",
                    {"parameters": sorted(parameters), "metrics": sorted(metrics)},
                )

    def ingest_metrics_file(self, run_id: str, path: Path) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HirakuError(f"Invalid metrics file for Run {run_id}: {exc}") from exc
        if not isinstance(raw, dict):
            raise HirakuError("Metrics file must contain a JSON object.")
        metrics = raw.get("metrics", {})
        parameters = raw.get("parameters", {})
        if not isinstance(metrics, dict) or not isinstance(parameters, dict):
            raise HirakuError("Metrics file 'metrics' and 'parameters' must be objects.")
        self.log(
            run_id,
            {str(k): str(v) for k, v in parameters.items()},
            {str(k): str(v) for k, v in metrics.items()},
        )

    def rows(self, goal_id: str | None = None, experiment_id: str | None = None) -> list[dict[str, Any]]:
        runs = self.repository.list(goal_id, experiment_id)
        with self.db.connect() as connection:
            result: list[dict[str, Any]] = []
            for row in runs:
                item = dict(row)
                item["provenance"] = json.loads(item.pop("provenance_json"))
                params = self.repository.parameters(connection, row["id"])
                metrics = self.repository.metrics(connection, row["id"])
                item.update({f"param/{entry['key']}": entry["value"] for entry in params})
                item.update({entry["key"]: entry["value"] for entry in metrics})
                result.append(item)
        return result

    @staticmethod
    def sort_rows(rows: list[dict[str, Any]], column: str, descending: bool) -> list[dict[str, Any]]:
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        unranked: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            try:
                number = float(row[column])
                if not math.isfinite(number):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                unranked.append(row)
            else:
                ranked.append((number, index, row))
        ranked.sort(key=lambda item: item[0], reverse=descending)
        output: list[dict[str, Any]] = []
        for rank, (_, _, row) in enumerate(ranked, start=1):
            output.append({**row, "_rank": rank})
        output.extend({**row, "_rank": None} for row in unranked)
        return output


class AnalysisService:
    def __init__(
        self,
        goals: GoalService,
        experiments: ExperimentService,
        runs: RunService,
        events: EventService | None = None,
    ):
        self.goals = goals
        self.experiments = experiments
        self.runs = runs
        self.events = events

    def verdict(self, reference: str) -> str:
        goal_id, experiment_id = split_experiment_ref(reference)
        goal = self.goals.get(goal_id)
        self.experiments.get(reference)
        baseline_ids = [item.id for item in self.experiments.list(goal_id) if item.kind == "baseline"]
        candidate = self._values(self.runs.rows(goal_id, experiment_id), goal.metric)
        baseline = self._values(
            [row for item in baseline_ids for row in self.runs.rows(goal_id, item)],
            goal.metric,
        )
        if not candidate or not baseline:
            return "inconclusive"
        if goal.direction == "max":
            return "supports" if max(candidate) > max(baseline) else "refutes"
        return "supports" if min(candidate) < min(baseline) else "refutes"

    def best(self, goal_id: str, exclude_experiment: str | None = None) -> dict[str, Any] | None:
        """Return the best completed metric value and its Experiment."""
        goal = self.goals.get(goal_id)
        candidates: list[tuple[float, Experiment, int]] = []
        for experiment in self.experiments.list(goal_id):
            if experiment.id == exclude_experiment or experiment.status == "abandoned":
                continue
            values = self._values(
                [row for row in self.runs.rows(goal_id, experiment.id) if row.get("status") == "completed"],
                goal.metric,
            )
            if values:
                value = max(values) if goal.direction == "max" else min(values)
                candidates.append((value, experiment, len(values)))
        if not candidates:
            return None
        value, experiment, count = (
            max(candidates, key=lambda item: item[0])
            if goal.direction == "max"
            else min(candidates, key=lambda item: item[0])
        )
        return {
            "goal_id": goal_id,
            "experiment_id": experiment.id,
            "metric": goal.metric,
            "value": value,
            "run_count": count,
            "commit": experiment.evaluated_commit,
        }

    def outcome(self, reference: str) -> str:
        goal_id, experiment_id = split_experiment_ref(reference)
        experiment = self.experiments.get(reference)
        if experiment.kind == "baseline":
            return "baseline"
        goal = self.goals.get(goal_id)
        values = self._values(
            [row for row in self.runs.rows(goal_id, experiment_id) if row.get("status") == "completed"],
            goal.metric,
        )
        if not values:
            return "invalid"
        best = self.best(goal_id, exclude_experiment=experiment_id)
        if best is None:
            return "improved"
        candidate = max(values) if goal.direction == "max" else min(values)
        return (
            "improved"
            if (candidate > best["value"] if goal.direction == "max" else candidate < best["value"])
            else "regressed"
        )

    def conclude(self, reference: str) -> Experiment:
        goal_id, _ = split_experiment_ref(reference)
        goal = self.goals.get(goal_id)
        verdict = self.verdict(reference)
        outcome = self.outcome(reference)
        experiment = self.experiments.conclude(reference, verdict, outcome)
        if verdict != "supports" and self.events:
            self.events.record(
                "experiment",
                reference,
                "research_result_failure",
                {"verdict": verdict, "metric": goal.metric},
            )
        return experiment

    @staticmethod
    def _values(rows: list[dict[str, Any]], metric: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            try:
                number = float(row[metric])
                if math.isfinite(number):
                    values.append(number)
            except (KeyError, TypeError, ValueError):
                continue
        return values


class LineageService:
    def __init__(self, experiments: ExperimentService):
        self.experiments = experiments

    def forest(self, goal_id: str) -> list[dict[str, Any]]:
        experiments = self.experiments.list(goal_id)
        by_parent: dict[str | None, list[Experiment]] = {}
        by_id = {experiment.id: experiment for experiment in experiments}
        for experiment in experiments:
            by_parent.setdefault(experiment.parent_id, []).append(experiment)

        for experiment in experiments:
            seen: set[str] = set()
            current: Experiment | None = experiment
            while current is not None:
                if current.id in seen:
                    raise HirakuError(f"Lineage cycle detected at '{current.id}'.")
                seen.add(current.id)
                current = by_id.get(current.parent_id) if current.parent_id else None

        def node(experiment: Experiment, path: frozenset[str]) -> dict[str, Any]:
            if experiment.id in path:
                raise HirakuError(f"Lineage cycle detected at '{experiment.id}'.")
            next_path = path | {experiment.id}
            return {
                **experiment.to_dict(),
                "children": [node(child, next_path) for child in by_parent.get(experiment.id, [])],
            }

        return [node(root, frozenset()) for root in by_parent.get(None, [])]


class EventService:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.db.transaction() as connection:
            record_event(connection, entity_type, entity_id, event_type, payload)

    def list(self, entity_id_prefix: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            if entity_id_prefix:
                rows = connection.execute(
                    "SELECT * FROM events WHERE entity_id=? OR entity_id LIKE ? ORDER BY id",
                    (entity_id_prefix, f"{entity_id_prefix}/%"),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result


class ProposalService:
    def __init__(self, db: Database, goals: GoalService):
        self.db = db
        self.goals = goals

    def create(
        self,
        goal_id: str,
        experiment_id: str,
        hypothesis: str,
        parent_experiment_id: str | None = None,
    ) -> dict[str, Any]:
        self.goals.get(goal_id)
        validate_id(experiment_id, "Experiment ID")
        if not hypothesis.strip():
            raise HirakuError("Proposal hypothesis cannot be empty.")
        if parent_experiment_id:
            validate_id(parent_experiment_id, "parent Experiment ID")
            with self.db.connect() as connection:
                parent = connection.execute(
                    "SELECT id FROM experiments WHERE goal_id=? AND id=?",
                    (goal_id, parent_experiment_id),
                ).fetchone()
            if parent is None:
                raise HirakuError(f"Parent Experiment '{goal_id}/{parent_experiment_id}' does not exist.")
        proposal_id = f"P-{uuid.uuid4().hex[:10]}"
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO proposals(id,goal_id,experiment_id,hypothesis,parent_experiment_id,"
                "status,created_at,admitted_experiment_id) VALUES (?,?,?,?,?,'pending',?,NULL)",
                (
                    proposal_id,
                    goal_id,
                    experiment_id,
                    hypothesis,
                    parent_experiment_id,
                    utc_now(),
                ),
            )
            record_event(connection, "proposal", proposal_id, "discovered")
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise HirakuError(f"Proposal '{proposal_id}' does not exist.")
        return dict(row)

    def list(self, goal_id: str, status: str | None = None) -> list[dict[str, Any]]:
        self.goals.get(goal_id)
        query = "SELECT * FROM proposals WHERE goal_id=?"
        arguments: list[str] = [goal_id]
        if status:
            query += " AND status=?"
            arguments.append(status)
        query += " ORDER BY created_at,id"
        with self.db.connect() as connection:
            rows = connection.execute(query, arguments).fetchall()
        return [dict(row) for row in rows]

    def admit(self, proposal_id: str, experiment_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            result = connection.execute(
                "UPDATE proposals SET status='admitted',admitted_experiment_id=? WHERE id=? AND status='pending'",
                (experiment_id, proposal_id),
            )
            if result.rowcount != 1:
                raise HirakuError(f"Proposal '{proposal_id}' is not pending.")
            record_event(
                connection,
                "proposal",
                proposal_id,
                "admitted",
                {"experiment": experiment_id},
            )
        return self.get(proposal_id)
