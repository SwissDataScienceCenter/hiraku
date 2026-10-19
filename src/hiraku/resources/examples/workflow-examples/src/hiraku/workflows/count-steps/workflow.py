"""Count steps: numbered steps with tokens, each started by the user."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from hiraku.errors import SubmissionRejected
from hiraku.markdown import render
from hiraku.workflow import Instruction, Workflow, reply


def _token() -> str:
    return secrets.token_hex(4)


class State(BaseModel):
    count: int
    step: int = 1
    token: str = Field(default_factory=_token)
    # running: the agent runs the current step; waiting: the step was
    # acknowledged and the user decides whether to go on; finished: done.
    phase: Literal["running", "waiting", "finished"] = "running"


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
        if state.phase == "finished":
            return Instruction(
                self.markdown.section("## Done"),
                context={"steps": state.step, "count": state.count},
                final=True,
            )
        if state.phase == "waiting":
            return Instruction(
                self.markdown.section("## Next"),
                context={
                    "done": state.step,
                    "remaining": state.count - state.step,
                },
                replies=[self.step, self.finish],
                requires_user_response=True,
            )
        # The step's values fill the {{placeholders}} of the section.
        values = {"step": state.step, "count": state.count, "token": state.token}
        return Instruction(
            render(self.markdown.section("## Step"), values),
            context=values,
            # Only the user ends the session, at the gate after a step.
            replies=[self.ack],
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
        state.phase = "finished" if state.step >= state.count else "waiting"

    @reply
    def step(self, state: State) -> None:
        """Run the next step."""
        state.step += 1
        state.token = _token()
        state.phase = "running"

    @reply
    def finish(self, state: State) -> None:
        """End the session after the current step."""
        state.phase = "finished"


WORKFLOW = CountSteps
