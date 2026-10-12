"""Autoresearch: the autonomous experiment loop with checked evidence."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.record import load_record, update_record
from hiraku.research import assess_run, comparison, find_run, improves, resolve_task
from hiraku.workflow import (
    Completion,
    Instruction,
    Session,
    Workflow,
    tool,
)

Decision = Literal["keep", "discard", "crash"]


class Result(BaseModel):
    """One assessed run and the decision it leads to."""

    record_id: str
    run_id: str
    commit: str
    value: float | None = None
    decision: Decision
    baseline: bool = False


class ResearchSession(Session):
    task: str
    iterations: int
    entrypoint: str
    metric: str
    direction: Literal["minimize", "maximize"]
    reference: Result | None = None
    results: list[Result] = Field(default_factory=list)
    pending: Result | None = None


class Autoresearch(Workflow):
    """Run the autoresearch loop autonomously for a fixed number of experiments.

    Each experiment is one idea, one commit, one experiment record, and one
    launch. Hiraku checks the tracked run, decides keep, discard, or crash by
    comparing the metric with the retained result, and ends the session when
    the budget is used.
    """

    success_status: str = "finished"
    """Run status that counts as a successful execution; set in front matter."""

    @tool
    def start(
        self,
        task: Annotated[
            str | None,
            Field(description="Task record ID; defaults to the active record's task."),
        ] = None,
        iterations: Annotated[
            int, Field(gt=0, description="Number of experiments after the baseline.")
        ] = 10,
        entrypoint: Annotated[
            str | None,
            Field(
                description="Entrypoint to launch; required if the task has several."
            ),
        ] = None,
        metric: Annotated[
            str | None,
            Field(description="Declared metric to compare; required if several."),
        ] = None,
        baseline_run: Annotated[
            str | None,
            Field(description="Existing successful run to use as the baseline."),
        ] = None,
    ) -> Instruction:
        """Start the autonomous loop on a task record."""
        record = resolve_task(self.config, task)
        chosen_entrypoint, chosen_metric, direction = comparison(
            record, self.config, entrypoint, metric
        )
        session = ResearchSession(
            task=record.id,
            iterations=iterations,
            entrypoint=chosen_entrypoint,
            metric=chosen_metric,
            direction=direction,
        )
        if baseline_run is None:
            return self._baseline(session)
        result = self._assess(session, baseline_run, require_new=False)
        if result.decision == "crash":
            raise SubmissionRejected(f"Run {baseline_run!r} is not a successful run")
        session.reference = result.model_copy(update={"baseline": True})
        return self._experiment(session)

    @tool
    def submit_run(
        self,
        session: ResearchSession,
        run_id: Annotated[str, Field(description="Tracked run ID of the launch.")],
    ) -> Instruction:
        """Submit the tracked run of the baseline or of the current experiment."""
        result = self._assess(session, run_id, require_new=True)
        if session.reference is None:
            result = result.model_copy(update={"baseline": True})
        elif result.decision == "keep" and not improves(
            result.value or 0.0, session.reference.value or 0.0, session.direction
        ):
            result = result.model_copy(update={"decision": "discard"})
        update_record(
            result.record_id,
            changes={
                "assessment": {
                    "session": session.session_id,
                    "run": result.run_id,
                    "metric": session.metric,
                    "value": result.value,
                    "reference_run": session.reference.run_id
                    if session.reference
                    else None,
                    "reference_value": session.reference.value
                    if session.reference
                    else None,
                    "decision": result.decision,
                }
            },
            config=self.config,
        )
        session.pending = result
        restore = (
            f"git reset --hard {session.reference.commit}"
            if session.reference is not None and result.decision != "keep"
            else None
        )
        return self.instruction(
            session,
            self.section("record"),
            context={
                "record_id": result.record_id,
                "decision": result.decision,
                "value": result.value,
                "reference": None
                if session.reference is None
                else session.reference.model_dump(),
                "restore_command": restore,
            },
            replies=[self.recorded, self.stop],
        )

    @tool
    def recorded(self, session: ResearchSession) -> Instruction | Completion:
        """Report that the decision and conclusion are written on the record."""
        result = session.pending
        if result is None:
            raise SubmissionRejected("No result is waiting to be recorded")
        fields = load_record(result.record_id, config=self.config).to_dict()
        if fields.get("decision") != result.decision:
            raise SubmissionRejected(
                f"Set decision: {result.decision} on record {result.record_id!r}"
            )
        if not str(fields.get("conclusion") or "").strip():
            raise SubmissionRejected(
                f"Write a conclusion on record {result.record_id!r}"
            )
        session.pending = None
        session.results.append(result)
        if result.decision == "keep":
            session.reference = result
        if session.reference is None:
            return self._baseline(session)
        if self._used(session) >= session.iterations:
            return self._finish(session, "completed", None)
        return self._experiment(session)

    @tool
    def stop(
        self,
        session: ResearchSession,
        reason: Annotated[str, Field(description="Why the loop stops early.")],
    ) -> Completion:
        """Stop the loop early and summarize the results."""
        return self._finish(session, "stopped", reason)

    def _baseline(self, session: ResearchSession) -> Instruction:
        return self.instruction(
            session,
            self.section("baseline"),
            context={
                "task": session.task,
                "entrypoint": session.entrypoint,
                "metric": session.metric,
                "direction": session.direction,
            },
            replies=[self.submit_run, self.stop],
        )

    def _experiment(self, session: ResearchSession) -> Instruction:
        return self.instruction(
            session,
            self.section("experiment"),
            context={
                "task": session.task,
                "experiment": self._used(session) + 1,
                "of": session.iterations,
                "entrypoint": session.entrypoint,
                "metric": session.metric,
                "direction": session.direction,
                "reference": session.reference.model_dump()
                if session.reference
                else None,
                "previous": [
                    item.model_dump(exclude={"commit"}) for item in session.results
                ],
            },
            replies=[self.submit_run, self.stop],
        )

    def _assess(
        self, session: ResearchSession, run_id: str, *, require_new: bool
    ) -> Result:
        task = load_record(session.task, config=self.config)
        run = find_run(task, self.config, run_id)
        if run is None:
            raise NotReady(
                f"Run {run_id!r} is not visible in the tracking backend yet; "
                "check again once the launch has finished"
            )
        if run.record_id is None:
            raise SubmissionRejected(f"Run {run_id!r} is not attributed to a record")
        record = load_record(run.record_id, config=self.config)
        if record.parent_id != session.task or record.type != "experiment":
            raise SubmissionRejected(
                f"Run {run_id!r} belongs to {record.id!r}, which is not an "
                f"experiment record under task {session.task!r}"
            )
        used = {item.record_id for item in session.results}
        if require_new and record.id in used:
            raise SubmissionRejected(
                f"Record {record.id!r} was already assessed; use a new experiment "
                "record for each attempt"
            )
        evidence = assess_run(
            run,
            record=record,
            entrypoint=session.entrypoint,
            metric=session.metric,
            success_status=self.success_status,
        )
        if evidence.outcome == "invalid":
            raise SubmissionRejected(
                f"{evidence.message}. Launch again with a new experiment record."
            )
        assert record.commit is not None
        return Result(
            record_id=record.id,
            run_id=run.id,
            commit=record.commit,
            value=evidence.value,
            decision="keep" if evidence.outcome == "valid" else "crash",
        )

    def _used(self, session: ResearchSession) -> int:
        return sum(not item.baseline for item in session.results)

    def _finish(
        self, session: ResearchSession, outcome: str, reason: str | None
    ) -> Completion:
        return self.completion(
            session,
            {
                "outcome": outcome,
                "reason": reason,
                "task": session.task,
                "metric": session.metric,
                "direction": session.direction,
                "experiments": self._used(session),
                "best": session.reference.model_dump() if session.reference else None,
                "results": [item.model_dump() for item in session.results],
            },
        )


WORKFLOW = Autoresearch
