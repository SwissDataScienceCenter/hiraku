"""Count steps: numbered steps with tokens, each started by the user."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import Field

from hiraku.errors import EventRejected
from hiraku.markdown import render
from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow


def _token() -> str:
    return secrets.token_hex(4)


class Counter(State):
    node: str = "running"
    count: int
    step: int = 1
    token: str = Field(default_factory=_token)


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and stops for the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. After each step the user
    decides whether to run the next one.
    """

    @start(dest="running")
    def start(
        self, *, count: Annotated[int, "Number of steps.", Field(gt=0)] = 5
    ) -> Counter:
        """Start counting steps to check the harness; runs the first step."""
        return Counter(count=count)

    @node("running")
    def run_step(self, counter: Counter) -> Instruction:
        values = {"step": counter.step, "count": counter.count, "token": counter.token}
        return Instruction(
            render(self.markdown.section("## Step"), values), context=values
        )

    @node("waiting")
    def ask_to_continue(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Next"),
            context={"done": counter.step, "remaining": counter.count - counter.step},
        )

    @node("finished", final=True)
    def report(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"steps": counter.step, "count": counter.count},
        )

    @event(source="running", dest=["waiting", "finished"])
    def ack(
        self,
        counter: Counter,
        *,
        token: Annotated[str, "Token of the current step."],
    ) -> Counter:
        """Acknowledge the current step with its token."""
        if token != counter.token:
            raise EventRejected(
                f"Token {token!r} does not match step {counter.step}; "
                "send the token from the current instruction"
            )
        counter.node = "finished" if counter.step >= counter.count else "waiting"
        return counter

    @event(source="waiting", dest="running")
    def step(self, counter: Counter) -> Counter:
        """Run the next step."""
        counter.step += 1
        counter.token = _token()
        counter.node = "running"
        return counter

    @event(source="waiting", dest="finished")
    def finish(self, counter: Counter) -> Counter:
        """End the session after the current step."""
        counter.node = "finished"
        return counter


WORKFLOW = CountSteps
