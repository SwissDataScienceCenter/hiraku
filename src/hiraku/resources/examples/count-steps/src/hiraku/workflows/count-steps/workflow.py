"""Count steps: numbered steps with tokens, each started by the user."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import BaseModel, Field

from hiraku.errors import SubmissionRejected
from hiraku.workflow import Instruction, Workflow, reply


def _token() -> str:
    return secrets.token_hex(4)


class State(BaseModel):
    count: int
    step: int = 1
    token: str = Field(default_factory=_token)
    # The current step was acknowledged; the user decides whether to go on.
    waiting: bool = False
    finished: bool = False


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and stops for the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. After each step the user
    decides whether to run the next one.
    """

    def start(
        self,
        count: Annotated[int, Field(gt=0, description="Number of steps.")] = 5,
    ) -> State:
        """Start counting steps to check the harness; runs the first step."""
        return State(count=count)

    def next(self, state: State) -> Instruction:
        if state.finished:
            return Instruction(
                self.markdown.section("## Done"),
                context={"steps": state.step, "count": state.count},
                final=True,
            )
        if state.waiting:
            return Instruction(
                self.markdown.section("## Next"),
                context={
                    "done": state.step,
                    "remaining": state.count - state.step,
                },
                replies=[self.step, self.finish],
                requires_user_response=True,
            )
        return Instruction(
            self.markdown.section("## Step"),
            context={
                "step": state.step,
                "count": state.count,
                "token": state.token,
                "line": f"Step {state.step}/{state.count}: token {state.token}",
            },
            replies=[self.ack, self.finish],
        )

    @reply
    def ack(
        self,
        state: State,
        token: Annotated[str, Field(description="Token of the current step.")],
    ) -> None:
        """Acknowledge the current step with its token."""
        if token != state.token:
            raise SubmissionRejected(
                f"Token {token!r} does not match step {state.step}; reply with "
                "the token from the current instruction"
            )
        state.finished = state.step >= state.count
        state.waiting = not state.finished

    @reply
    def step(self, state: State) -> None:
        """Run the next step."""
        state.step += 1
        state.token = _token()
        state.waiting = False

    @reply
    def finish(self, state: State) -> None:
        """End the session early."""
        state.finished = True


WORKFLOW = CountSteps
