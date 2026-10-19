"""Count steps: numbered steps with tokens, each approved by the user."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import BaseModel, Field

from hiraku.errors import EventRejected
from hiraku.markdown import render
from hiraku.workflow import Instruction, Workflow, event, state


def _token() -> str:
    return secrets.token_hex(4)


class Counter(BaseModel):
    count: int
    step: int = 1
    token: str = Field(default_factory=_token)


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and asks the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. The user approves each
    step's acknowledgement before the next step runs, and the end of the
    session.
    """

    def start(
        self,
        count: Annotated[int, Field(gt=0, description="Number of steps.")] = 5,
    ) -> Counter:
        """Start counting steps to check the harness; runs the first step."""
        return Counter(count=count)

    # States: the agent runs one step after another until the last.

    @state("running", start=True)
    def run_step(self, counter: Counter) -> Instruction:
        # The step's values fill the {{placeholders}} of the section.
        values = {"step": counter.step, "count": counter.count, "token": counter.token}
        return Instruction(
            render(self.markdown.section("## Step"), values), context=values
        )

    @state("finished", final=True)
    def report(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"steps": counter.step, "count": counter.count},
        )

    # Events: the user approves each one, so only the user goes on or finishes.

    @event(source="running", dest=["running", "finished"], requires_approval=True)
    def ack(
        self,
        counter: Counter,
        token: Annotated[str, Field(description="Token of the current step.")],
    ) -> str:
        """Acknowledge the current step with its token, and run the next one."""
        if token != counter.token:
            raise EventRejected(
                f"Token {token!r} does not match step {counter.step}; send the "
                "token from the current instruction"
            )
        if counter.step >= counter.count:
            return "finished"
        counter.step += 1
        counter.token = _token()
        return "running"

    @event(source="running", dest="finished", requires_approval=True)
    def finish(self, counter: Counter) -> None:
        """End the session without acknowledging the current step."""
        counter.step -= 1


WORKFLOW = CountSteps
