"""Count steps: numbered steps with tokens and one approval gate."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import Field

from hiraku.errors import SubmissionRejected
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply

GATE_STEP = 5


class CountStepsSession(Session):
    count: int
    step: int = 0
    token: str = ""


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and gates.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. Step 5 is a user gate.
    """

    def start(
        self,
        count: Annotated[int, Field(gt=0, description="Number of steps.")] = 10,
    ) -> Instruction | Completion:
        """Start counting steps to check the harness."""
        return self._next(CountStepsSession(count=count))

    @reply
    def ack(
        self,
        session: CountStepsSession,
        token: Annotated[str, Field(description="Token of the current step.")],
    ) -> Instruction | Completion:
        """Acknowledge the current step with its token."""
        self._check(session, token)
        return self._next(session)

    @reply
    def proceed(
        self,
        session: CountStepsSession,
        token: Annotated[str, Field(description="Token of the gate step.")],
    ) -> Instruction | Completion:
        """Continue after the user approved the plan at the gate."""
        self._check(session, token)
        return self._next(session)

    @reply
    def finish(self, session: CountStepsSession) -> Completion:
        """End the session early."""
        return self._done(session)

    def _check(self, session: CountStepsSession, token: str) -> None:
        if token != session.token:
            raise SubmissionRejected(
                f"Token {token!r} does not match step {session.step}; reply with "
                "the token from the current instruction"
            )

    def _next(self, session: CountStepsSession) -> Instruction | Completion:
        if session.step >= session.count:
            return self._done(session)
        session.step += 1
        session.token = secrets.token_hex(4)
        context = {
            "step": session.step,
            "count": session.count,
            "token": session.token,
            "line": f"Step {session.step}/{session.count}: token {session.token}",
        }
        if session.step == GATE_STEP:
            return self.instruction(
                session,
                self.section("gate"),
                context={**context, "remaining": session.count - session.step},
                requires_user_response=True,
                replies=[self.proceed, self.finish],
            )
        return self.instruction(
            session,
            self.section("step"),
            context=context,
            replies=[self.ack, self.finish],
        )

    def _done(self, session: CountStepsSession) -> Completion:
        return self.completion(session, {"steps": session.step, "count": session.count})


WORKFLOW = CountSteps
