"""Count steps: numbered steps with tokens, each started by the user."""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import Field

from hiraku.errors import EventRejected
from hiraku.markdown import render
from hiraku.workflows import Arguments, Instruction, State, Workflow, event, phase


def _token() -> str:
    return secrets.token_hex(4)


class Counter(State):
    phase: str = "running"
    count: int
    step: int = 1
    token: str = Field(default_factory=_token)


class StartArguments(Arguments):
    count: Annotated[int, Field(gt=0, description="Number of steps.")] = 5


class AckArguments(Arguments):
    token: Annotated[str, Field(description="Token of the current step.")]


class CountSteps(Workflow):
    """Check that the harness follows numbered instructions and stops for the user.

    Each step sends a fresh random token that the harness must echo back, so a
    skipped, repeated, or invented step is rejected. After each step the user
    decides whether to run the next one.
    """

    @event(source=None, dest="running")
    def start(self, args: StartArguments) -> Counter:
        """Start counting steps to check the harness; runs the first step."""
        count = args.count
        return Counter(count=count)

    @phase("running")
    def run_step(self, counter: Counter) -> Instruction:
        values = {"step": counter.step, "count": counter.count, "token": counter.token}
        return Instruction(
            render(self.markdown.section("## Step"), values), context=values
        )

    @phase("waiting")
    def ask_to_continue(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Next"),
            context={"done": counter.step, "remaining": counter.count - counter.step},
        )

    @phase("finished", final=True)
    def report(self, counter: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"steps": counter.step, "count": counter.count},
        )

    @event(source="running", dest=["waiting", "finished"])
    def ack(self, counter: Counter, args: AckArguments) -> Counter:
        """Acknowledge the current step with its token."""
        token = args.token
        if token != counter.token:
            raise EventRejected(
                f"Token {token!r} does not match step {counter.step}; "
                "send the token from the current instruction"
            )
        counter = counter.model_copy(
            update={"phase": "finished" if counter.step >= counter.count else "waiting"}
        )
        return counter

    @event(source="waiting", dest="running")
    def step(self, counter: Counter) -> Counter:
        """Run the next step."""
        counter = counter.model_copy(update={"step": counter.step + 1})
        counter = counter.model_copy(update={"token": _token()})
        return counter.model_copy(update={"phase": "running"})

    @event(source="waiting", dest="finished")
    def finish(self, counter: Counter) -> Counter:
        """End the session after the current step."""
        return counter.model_copy(update={"phase": "finished"})


WORKFLOW = CountSteps
