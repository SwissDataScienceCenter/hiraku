"""Count steps: numbered steps with tokens, each started by the user."""

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
    """Check that the harness follows numbered instructions and stops for the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. After each step the user
    decides whether to run the next one.
    """

    def start(
        self,
        count: Annotated[int, Field(gt=0, description="Number of steps.")] = 5,
    ) -> Counter:
        """Start counting steps to check the harness; runs the first step."""
        return Counter(count=count)

    # States: the agent runs a step, then the user decides at the gate.

    @state("running", start=True)
    def run_step(self, counter: Counter) -> Instruction:
        # The step's values fill the {{placeholders}} of the section.
        values = {"step": counter.step, "count": counter.count, "token": counter.token}
        return Instruction(
            render(self.markdown.section("## Step"), values), context=values
        )

    @state("waiting")
    def ask_to_continue(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Next"),
            context={"done": counter.step, "remaining": counter.count - counter.step},
            requires_user_response=True,
        )

    @state("finished", final=True)
    def report(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"steps": counter.step, "count": counter.count},
        )

    # Events: only the user goes on or finishes, at the gate after a step.

    @event(source="running", dest=["waiting", "finished"])
    def ack(
        self,
        counter: Counter,
        token: Annotated[str, Field(description="Token of the current step.")],
    ) -> str:
        """Acknowledge the current step with its token."""
        if token != counter.token:
            raise EventRejected(
                f"Token {token!r} does not match step {counter.step}; send the "
                "token from the current instruction"
            )
        return "finished" if counter.step >= counter.count else "waiting"

    @event(source="waiting", dest="running")
    def step(self, counter: Counter) -> None:
        """Run the next step."""
        counter.step += 1
        counter.token = _token()

    @event(source="waiting", dest="finished")
    def finish(self, counter: Counter) -> None:
        """End the session after the current step."""


WORKFLOW = CountSteps
