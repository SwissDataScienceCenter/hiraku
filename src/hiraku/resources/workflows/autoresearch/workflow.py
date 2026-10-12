"""Autoresearch: bounded, evidence-checked research iterations on a task record."""

from __future__ import annotations

import math
import shlex
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.launch import render_arguments
from hiraku.record import Record, load_record, update_record
from hiraku.run import Run
from hiraku.workflow import Completion, Instruction, Session, Workflow, tool

COUNTED_OUTCOMES = frozenset({"assessed", "failed", "declined", "skipped"})


class Attempt(BaseModel):
    """One selected candidate and what became of it."""

    record_id: str
    outcome: Literal[
        "selected", "assessed", "failed", "declined", "skipped", "stopped"
    ] = "selected"
    commit: str | None = None
    run_id: str | None = None
    value: float | None = None
    improved: bool | None = None
    note: str | None = None


class Proposal(BaseModel):
    """A concrete launch configuration awaiting approval or evidence."""

    purpose: Literal["baseline", "attempt"]
    record_id: str
    commit: str
    entrypoint: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class Reference(BaseModel):
    """A run whose metric value is the comparison reference."""

    run_id: str
    record_id: str
    value: float


class Conclusion(BaseModel):
    """A record that must receive the harness's written conclusion."""

    purpose: Literal["baseline", "attempt"]
    record_id: str


class ResearchSession(Session):
    task: str
    iterations: int
    candidates: int
    collaborative: bool
    entrypoint: str
    metric: str
    direction: Literal["minimize", "maximize"]
    reference: Reference | None = None
    reference_history: list[Reference] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    proposed_ids: list[str] = Field(default_factory=list)
    feedback: str | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    proposal: Proposal | None = None
    conclusion: Conclusion | None = None


class Autoresearch(Workflow):
    """Run a bounded number of research iterations on a task.

    Each iteration proposes several hypotheses as experiment records, selects
    one, implements and launches it, and compares its tracked metric against
    the retained reference result. Collaborative sessions ask the user to
    choose candidates and approve launches; autonomous sessions decide alone.
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
            int, Field(gt=0, description="Selected attempts to run.")
        ] = 5,
        candidates: Annotated[
            int, Field(gt=0, description="Hypotheses proposed per iteration.")
        ] = 3,
        collaborative: Annotated[
            bool,
            Field(
                description="Ask the user to select candidates and approve launches."
            ),
        ] = True,
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
    ) -> Instruction | Completion:
        """Start autoresearch on a task record."""
        record = self._task(task)
        values = record.resolve(config=self.config).values
        entrypoints = sorted((values.get("entrypoints") or {}).keys())
        selected_entrypoint = _choose(entrypoint, entrypoints, "entrypoint")
        declarations = {item.name: item for item in values.get("metrics") or []}
        selected_metric = _choose(metric, sorted(declarations), "metric")
        session = ResearchSession(
            task=record.id,
            iterations=iterations,
            candidates=candidates,
            collaborative=collaborative,
            entrypoint=selected_entrypoint,
            metric=selected_metric,
            direction=declarations[selected_metric].direction,
        )
        if baseline_run is None:
            return self._baseline_instruction(session)
        session.reference = self._existing_reference(session, baseline_run)
        session.reference_history.append(session.reference)
        return self._generate(session)

    # Baseline -----------------------------------------------------------------

    @tool
    def use_baseline(self, session: ResearchSession, run_id: str) -> Instruction:
        """Use an existing successful run as the baseline."""
        session.reference = self._existing_reference(session, run_id)
        session.reference_history.append(session.reference)
        return self._generate(session)

    @tool
    def establish_baseline(
        self,
        session: ResearchSession,
        record_id: Annotated[
            str,
            Field(description="Baseline experiment record bound to its commit."),
        ],
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="Entrypoint argument overrides for the launch."),
        ] = None,
    ) -> Instruction:
        """Propose launching a baseline experiment record."""
        record = self._experiment(session, record_id)
        session.proposal = self._proposal(session, "baseline", record, arguments)
        return self._approval_or_launch(session)

    # Iteration ----------------------------------------------------------------

    @tool
    def proposed(
        self,
        session: ResearchSession,
        record_ids: Annotated[
            list[str], Field(description="IDs of the new experiment records.")
        ],
    ) -> Instruction:
        """Submit the newly created candidate experiment records."""
        if len(record_ids) != session.candidates:
            raise SubmissionRejected(
                f"Expected {session.candidates} candidate records, got "
                f"{len(record_ids)}"
            )
        if len(set(record_ids)) != len(record_ids):
            raise SubmissionRejected("Candidate record IDs must be distinct")
        records = []
        for record_id in record_ids:
            if record_id in session.proposed_ids:
                raise SubmissionRejected(
                    f"Record {record_id!r} was already proposed in this session; "
                    "create a new record for each candidate"
                )
            records.append(self._experiment(session, record_id))
        session.candidate_ids = list(record_ids)
        session.proposed_ids.extend(record_ids)
        session.feedback = None
        return self.instruction(
            session,
            self.prompt("#select"),
            context={
                "iteration": self._iteration(session),
                "candidates": [_summary(record) for record in records],
            },
            requires_user_response=session.collaborative,
            replies=[self.select, self.revise, self.stop],
        )

    @tool
    def select(
        self,
        session: ResearchSession,
        record_id: Annotated[str, Field(description="Selected candidate record ID.")],
        rationale: Annotated[
            str, Field(description="Why this candidate; required when autonomous.")
        ] = "",
    ) -> Instruction:
        """Select one candidate to implement."""
        if record_id not in session.candidate_ids:
            raise SubmissionRejected(
                f"{record_id!r} is not one of the candidates: "
                f"{', '.join(session.candidate_ids)}"
            )
        if not session.collaborative and not rationale.strip():
            raise SubmissionRejected("Explain the selection in rationale")
        if rationale.strip():
            update_record(
                record_id,
                changes={"selection_rationale": rationale.strip()},
                config=self.config,
            )
        session.attempts.append(Attempt(record_id=record_id))
        session.candidate_ids = []
        return self.instruction(
            session,
            self.prompt("#implement"),
            context={"record_id": record_id, "task": session.task},
            replies=[self.implemented, self.skip, self.stop],
        )

    @tool
    def revise(
        self,
        session: ResearchSession,
        feedback: Annotated[str, Field(description="What to change in the proposals.")],
    ) -> Instruction:
        """Ask for new candidates instead of selecting one."""
        session.feedback = feedback
        session.candidate_ids = []
        return self._generate(session)

    @tool
    def implemented(
        self,
        session: ResearchSession,
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="Entrypoint argument overrides for the launch."),
        ] = None,
    ) -> Instruction:
        """Report that the selected candidate is implemented and committed."""
        attempt = self._current_attempt(session)
        record = load_record(attempt.record_id, config=self.config)
        session.proposal = self._proposal(session, "attempt", record, arguments)
        attempt.commit = session.proposal.commit
        return self._approval_or_launch(session)

    # Launch -------------------------------------------------------------------

    @tool
    def approve(self, session: ResearchSession) -> Instruction:
        """Approve the presented launch configuration as saved."""
        proposal = self._pending_proposal(session)
        proposal.approved = True
        return self._launch_instruction(session)

    @tool
    def decline(
        self,
        session: ResearchSession,
        reason: Annotated[str, Field(description="Why the launch is declined.")],
    ) -> Instruction | Completion:
        """Decline the launch; an attempt counts toward the iteration budget."""
        proposal = self._pending_proposal(session)
        session.proposal = None
        if proposal.purpose == "baseline":
            return self._baseline_instruction(
                session, issue=f"The baseline launch was declined: {reason}"
            )
        attempt = self._current_attempt(session)
        attempt.outcome, attempt.note = "declined", reason
        return self._next(session)

    @tool
    def change_configuration(
        self,
        session: ResearchSession,
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="New entrypoint argument overrides."),
        ] = None,
    ) -> Instruction:
        """Change the launch configuration before launching; needs new approval."""
        proposal = self._pending_proposal(session)
        record = load_record(proposal.record_id, config=self.config)
        session.proposal = self._proposal(session, proposal.purpose, record, arguments)
        if proposal.purpose == "attempt":
            self._current_attempt(session).commit = session.proposal.commit
        return self._approval_or_launch(session)

    @tool
    def submit_run(
        self,
        session: ResearchSession,
        run_id: Annotated[str, Field(description="Tracked run ID of the launch.")],
    ) -> Instruction:
        """Submit the tracked run produced by the approved launch."""
        proposal = self._pending_proposal(session)
        if not proposal.approved:
            raise SubmissionRejected("The launch configuration is not approved yet")
        run = self._find_run(session, run_id)
        if run is None:
            raise NotReady(
                f"Run {run_id!r} is not visible in the tracking backend yet. Check "
                f"the runs of record {proposal.record_id!r} and reply again once "
                "it appears."
            )
        kind, message, value = self._assess_evidence(session, run, proposal)
        if kind == "review":
            return self._review(session, message, run_id)
        if proposal.purpose == "baseline":
            if kind == "failed":
                session.proposal = None
                return self._baseline_instruction(session, issue=message)
            assert value is not None
            session.reference = Reference(
                run_id=run.id, record_id=proposal.record_id, value=value
            )
            session.reference_history.append(session.reference)
            self._record_assessment(session, proposal, run, value, improved=None)
            return self._conclude(session, proposal)
        attempt = self._current_attempt(session)
        attempt.run_id = run.id
        if kind == "failed":
            attempt.outcome, attempt.note = "failed", message
            self._record_assessment(session, proposal, run, None, improved=None)
            return self._conclude(session, proposal)
        assert value is not None and session.reference is not None
        improved = _better(value, session.reference.value, session.direction)
        attempt.outcome, attempt.value, attempt.improved = "assessed", value, improved
        self._record_assessment(session, proposal, run, value, improved=improved)
        if improved:
            session.reference = Reference(
                run_id=run.id, record_id=proposal.record_id, value=value
            )
            session.reference_history.append(session.reference)
        return self._conclude(session, proposal)

    @tool
    def report_failure(
        self,
        session: ResearchSession,
        details: Annotated[
            str, Field(description="What failed; no tracked run was recorded.")
        ],
    ) -> Instruction | Completion:
        """Report that the launch failed without producing a tracked run."""
        proposal = self._pending_proposal(session)
        if proposal.purpose == "baseline":
            session.proposal = None
            return self._baseline_instruction(
                session, issue=f"The baseline launch failed: {details}"
            )
        attempt = self._current_attempt(session)
        attempt.outcome, attempt.note = "failed", details
        update_record(
            proposal.record_id,
            changes={
                "assessment": {
                    "session": session.session_id,
                    "status": "launch_failed",
                    "details": details,
                }
            },
            config=self.config,
        )
        return self._conclude(session, proposal)

    @tool
    def skip(
        self,
        session: ResearchSession,
        reason: Annotated[str, Field(description="Why the attempt is skipped.")],
    ) -> Instruction | Completion:
        """Skip the selected attempt; it stays unassessed and counts as used."""
        attempt = self._current_attempt(session)
        attempt.outcome, attempt.note = "skipped", reason
        session.proposal = None
        return self._next(session)

    @tool
    def concluded(self, session: ResearchSession) -> Instruction | Completion:
        """Report that the record's evidence-backed conclusion is written."""
        if session.conclusion is None:
            raise SubmissionRejected("No conclusion is pending")
        record = load_record(session.conclusion.record_id, config=self.config)
        conclusion = record.to_dict().get("conclusion")
        if not isinstance(conclusion, str) or not conclusion.strip():
            raise SubmissionRejected(
                f"Record {record.id!r} has no conclusion field yet; write it with "
                "record_update before replying"
            )
        purpose = session.conclusion.purpose
        session.conclusion = None
        if purpose == "baseline":
            return self._generate(session)
        return self._next(session)

    @tool
    def stop(
        self,
        session: ResearchSession,
        reason: Annotated[str, Field(description="Why the research stops early.")],
    ) -> Completion:
        """Stop the session early and summarize what was done."""
        for attempt in session.attempts:
            if attempt.outcome == "selected":
                attempt.outcome, attempt.note = "stopped", reason
        session.proposal = None
        session.conclusion = None
        return self._complete(session, "stopped", reason)

    # Instructions -------------------------------------------------------------

    def _baseline_instruction(
        self, session: ResearchSession, issue: str | None = None
    ) -> Instruction:
        return self.instruction(
            session,
            self.prompt("#baseline"),
            context={
                "task": session.task,
                "entrypoint": session.entrypoint,
                "metric": session.metric,
                "direction": session.direction,
                "issue": issue,
            },
            requires_user_response=session.collaborative,
            replies=[self.use_baseline, self.establish_baseline, self.stop],
        )

    def _generate(self, session: ResearchSession) -> Instruction:
        return self.instruction(
            session,
            self.prompt("#generate"),
            context={
                "task": session.task,
                "iteration": self._iteration(session),
                "count": session.candidates,
                "metric": session.metric,
                "direction": session.direction,
                "reference": _dump(session.reference),
                "previous_attempts": [
                    attempt.model_dump(exclude_none=True)
                    for attempt in session.attempts
                ],
                "feedback": session.feedback,
            },
            replies=[self.proposed, self.stop],
        )

    def _approval_or_launch(self, session: ResearchSession) -> Instruction:
        proposal = self._pending_proposal(session)
        if not session.collaborative:
            proposal.approved = True
        if proposal.approved:
            return self._launch_instruction(session)
        return self.instruction(
            session,
            self.prompt("#approve"),
            context={
                **proposal.model_dump(exclude={"approved"}),
                "command": _command(proposal),
            },
            requires_user_response=True,
            replies=[self.approve, self.decline, self.change_configuration, self.stop],
        )

    def _launch_instruction(self, session: ResearchSession) -> Instruction:
        proposal = self._pending_proposal(session)
        return self.instruction(
            session,
            self.prompt("#launch"),
            context={
                **proposal.model_dump(exclude={"approved"}),
                "command": _command(proposal),
            },
            replies=[
                self.submit_run,
                self.report_failure,
                self.change_configuration,
                self.stop,
            ],
        )

    def _review(self, session: ResearchSession, issue: str, run_id: str) -> Instruction:
        proposal = self._pending_proposal(session)
        replies: list[Callable[..., Any]] = [
            self.submit_run,
            self.change_configuration,
            self.stop,
        ]
        if proposal.purpose == "attempt":
            replies.insert(1, self.skip)
        return self.instruction(
            session,
            self.prompt("#review"),
            context={
                "issue": issue,
                "run_id": run_id,
                "approved": proposal.model_dump(exclude={"approved"}),
            },
            requires_user_response=True,
            replies=replies,
        )

    def _conclude(self, session: ResearchSession, proposal: Proposal) -> Instruction:
        session.proposal = None
        session.conclusion = Conclusion(
            purpose=proposal.purpose, record_id=proposal.record_id
        )
        record = load_record(proposal.record_id, config=self.config)
        return self.instruction(
            session,
            self.prompt("#conclude"),
            context={
                "record_id": record.id,
                "assessment": record.to_dict().get("assessment"),
                "reference": _dump(session.reference),
            },
            replies=[self.concluded, self.stop],
        )

    def _next(self, session: ResearchSession) -> Instruction | Completion:
        used = sum(item.outcome in COUNTED_OUTCOMES for item in session.attempts)
        if used >= session.iterations:
            return self._complete(session, "completed", None)
        return self._generate(session)

    def _complete(
        self, session: ResearchSession, outcome: str, reason: str | None
    ) -> Completion:
        used = sum(item.outcome in COUNTED_OUTCOMES for item in session.attempts)
        return self.completion(
            session,
            {
                "outcome": outcome,
                "reason": reason,
                "task": session.task,
                "metric": session.metric,
                "direction": session.direction,
                "iterations": {"budget": session.iterations, "used": used},
                "baseline": _dump(
                    session.reference_history[0] if session.reference_history else None
                ),
                "best": _dump(session.reference),
                "attempts": [
                    attempt.model_dump(exclude_none=True)
                    for attempt in session.attempts
                ],
                "unresolved": [
                    attempt.record_id
                    for attempt in session.attempts
                    if attempt.outcome in {"skipped", "stopped", "selected"}
                ],
            },
        )

    # Checks -------------------------------------------------------------------

    def _task(self, task: str | None) -> Record:
        if task is not None:
            record = load_record(task, config=self.config)
            if record.type != "task":
                raise SubmissionRejected(f"Record {task!r} is not a task record")
            return record
        active = self.config.active_record_id
        if active is None:
            raise SubmissionRejected("No active record; pass the task record ID")
        record: Record | None = load_record(active, config=self.config)
        while record is not None:
            if record.type == "task":
                return record
            parent = record.parent_id
            record = load_record(parent, config=self.config) if parent else None
        raise SubmissionRejected(
            f"Active record {active!r} has no task ancestor; pass the task record ID"
        )

    def _experiment(self, session: ResearchSession, record_id: str) -> Record:
        record = load_record(record_id, config=self.config)
        if record.parent_id != session.task:
            raise SubmissionRejected(
                f"Record {record_id!r} must be a child of task {session.task!r}"
            )
        if record.type != "experiment":
            raise SubmissionRejected(
                f"Record {record_id!r} must have type 'experiment'"
            )
        return record

    def _proposal(
        self,
        session: ResearchSession,
        purpose: Literal["baseline", "attempt"],
        record: Record,
        arguments: dict[str, Any] | None,
    ) -> Proposal:
        if record.commit is None:
            raise SubmissionRejected(
                f"Record {record.id!r} has no bound commit. Commit the "
                "implementation and bind it with record_update (commit: HEAD)."
            )
        record.entrypoint(session.entrypoint, config=self.config)
        return Proposal(
            purpose=purpose,
            record_id=record.id,
            commit=record.commit,
            entrypoint=session.entrypoint,
            arguments=dict(arguments or {}),
        )

    def _pending_proposal(self, session: ResearchSession) -> Proposal:
        if session.proposal is None:
            raise SubmissionRejected("No launch is pending in this session")
        return session.proposal

    def _current_attempt(self, session: ResearchSession) -> Attempt:
        if session.attempts and session.attempts[-1].outcome == "selected":
            return session.attempts[-1]
        raise SubmissionRejected("No selected attempt is in progress")

    def _iteration(self, session: ResearchSession) -> dict[str, int]:
        used = sum(item.outcome in COUNTED_OUTCOMES for item in session.attempts)
        return {"current": used + 1, "of": session.iterations}

    def _find_run(self, session: ResearchSession, run_id: str) -> Run | None:
        task = load_record(session.task, config=self.config)
        backend = task.resolve_tracking_backend(config=self.config)
        return next(
            (run for run in backend.list_runs(record_id=None) if run.id == run_id),
            None,
        )

    def _existing_reference(self, session: ResearchSession, run_id: str) -> Reference:
        run = self._find_run(session, run_id)
        if run is None:
            raise SubmissionRejected(f"Run {run_id!r} was not found")
        if run.entrypoint != session.entrypoint:
            raise SubmissionRejected(
                f"Run {run_id!r} used entrypoint {run.entrypoint!r}, not "
                f"{session.entrypoint!r}; it is not comparable"
            )
        if run.record_id is None:
            raise SubmissionRejected(f"Run {run_id!r} is not attributed to a record")
        kind, message, value = self._assess_evidence(session, run, None)
        if kind is not None or value is None:
            raise SubmissionRejected(f"Run {run_id!r} cannot be a baseline: {message}")
        return Reference(run_id=run.id, record_id=run.record_id, value=value)

    def _assess_evidence(
        self, session: ResearchSession, run: Run, proposal: Proposal | None
    ) -> tuple[Literal["review", "failed"] | None, str, float | None]:
        """Classify a run as valid evidence, a failed attempt, or needing review."""
        if proposal is not None:
            expected = {
                "record": (run.record_id, proposal.record_id),
                "commit": (run.commit, proposal.commit),
                "entrypoint": (run.entrypoint, proposal.entrypoint),
            }
            for name, (actual, approved) in expected.items():
                if actual != approved:
                    return (
                        "review",
                        f"Run {run.id!r} has {name} {actual!r}, but the approved "
                        f"launch used {approved!r}",
                        None,
                    )
            for name, approved in proposal.arguments.items():
                tracked = _tracked_parameter(run, name)
                if tracked is not None and str(tracked) != str(approved):
                    return (
                        "review",
                        f"Run {run.id!r} tracked {name}={tracked!r}, but the approved "
                        f"launch used {approved!r}",
                        None,
                    )
        # Backends may report absent values as empty strings.
        status = run.raw.get("status") or None
        if status is None:
            return (
                "review",
                f"Run {run.id!r} records no execution status, so its success "
                "cannot be verified",
                None,
            )
        if status != self.success_status:
            return "failed", f"Run {run.id!r} ended with status {status!r}", None
        git_state = run.raw.get("git_state") or None
        if git_state is not None and git_state != "clean":
            return (
                "review",
                f"Run {run.id!r} ran from a {git_state} working tree, so its commit "
                "does not identify the evaluated code",
                None,
            )
        value = _finite(run.metrics.get(session.metric))
        if value is None:
            return (
                "review",
                f"Run {run.id!r} has no finite value for metric {session.metric!r}",
                None,
            )
        return None, "valid", value

    def _record_assessment(
        self,
        session: ResearchSession,
        proposal: Proposal,
        run: Run,
        value: float | None,
        *,
        improved: bool | None,
    ) -> None:
        # Called before an improvement replaces the reference, so this is the
        # result the attempt was compared against.
        reference = session.reference
        assessment: dict[str, Any] = {
            "session": session.session_id,
            "purpose": proposal.purpose,
            "run": run.id,
            "commit": run.commit,
            "status": run.raw.get("status") or None,
            "metric": session.metric,
            "direction": session.direction,
            "value": value,
        }
        if proposal.purpose == "attempt" and reference is not None:
            assessment["reference_run"] = reference.run_id
            assessment["reference_value"] = reference.value
            assessment["improved"] = improved
        update_record(
            proposal.record_id,
            changes={"assessment": assessment},
            config=self.config,
        )


def _choose(requested: str | None, available: list[str], label: str) -> str:
    if requested is not None:
        if requested not in available:
            raise SubmissionRejected(
                f"Unknown {label} {requested!r}; available: {', '.join(available)}"
            )
        return requested
    if len(available) == 1:
        return available[0]
    if not available:
        raise SubmissionRejected(f"The task declares no {label}")
    raise SubmissionRejected(
        f"The task declares several {label}s ({', '.join(available)}); pass {label}"
    )


def _better(value: float, reference: float, direction: str) -> bool:
    return value < reference if direction == "minimize" else value > reference


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _tracked_parameter(run: Run, name: str) -> Any:
    for key in (name, name.replace("-", "_"), name.replace("_", "-")):
        if key in run.params:
            return run.params[key]
    return None


def _command(proposal: Proposal) -> str:
    command = [
        "hiraku",
        "launch",
        proposal.entrypoint,
        "--record",
        proposal.record_id,
        "--commit",
        proposal.commit,
    ]
    if proposal.arguments:
        command.extend(["--", *render_arguments(proposal.arguments)])
    return shlex.join(command)


def _summary(record: Record) -> dict[str, Any]:
    values = record.to_dict()
    return {
        "id": record.id,
        **{
            key: values[key]
            for key in ("summary", "hypothesis", "rationale")
            if key in values
        },
    }


def _dump(model: BaseModel | None) -> dict[str, Any] | None:
    return None if model is None else model.model_dump()


WORKFLOW = Autoresearch
