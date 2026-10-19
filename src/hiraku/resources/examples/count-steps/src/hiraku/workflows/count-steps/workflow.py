"""Count steps: numbered steps with tokens, each started by the user."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import Field

from hiraku.errors import SubmissionRejected
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply


class CountStepsSession(Session):
    count: int
    step: int = 0
    token: str = ""


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and stops for the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. After each step the user
    decides whether to run the next one.
    """

    def start(
        self,
        count: Annotated[int, Field(gt=0, description="Number of steps.")] = 5,
    ) -> Instruction:
        """Start counting steps to check the harness; runs the first step."""
        return self._step(CountStepsSession(count=count))

    @reply
    def step(self, session: CountStepsSession) -> Instruction:
        """Run the next step."""
        return self._step(session)

    @reply
    def ack(
        self,
        session: CountStepsSession,
        token: Annotated[str, Field(description="Token of the current step.")],
    ) -> Instruction | Completion:
        """Acknowledge the current step with its token."""
        if token != session.token:
            raise SubmissionRejected(
                f"Token {token!r} does not match step {session.step}; reply with "
                "the token from the current instruction"
            )
        if session.step >= session.count:
            return self._done(session)
        return self.instruction(
            session,
            self.markdown.section("## Next"),
            context={
                "done": session.step,
                "remaining": session.count - session.step,
            },
            requires_user_response=True,
            replies=[self.step, self.finish],
        )

    @reply
    def finish(self, session: CountStepsSession) -> Completion:
        """End the session early."""
        return self._done(session)

    def _step(self, session: CountStepsSession) -> Instruction:
        session.step += 1
        session.token = secrets.token_hex(4)
        return self.instruction(
            session,
            self.markdown.section("## Step"),
            context={
                "step": session.step,
                "count": session.count,
                "token": session.token,
                "line": f"Step {session.step}/{session.count}: token {session.token}",
            },
            replies=[self.ack, self.finish],
        )

    def _done(self, session: CountStepsSession) -> Completion:
        return self.completion(session, {"steps": session.step, "count": session.count})


WORKFLOW = CountSteps
