from typing import Annotated

from pydantic import Field

from hiraku.markdown import render
from hiraku.workflows import Arguments, Instruction, State, Workflow, event, phase


class Inputs(State):
    phase: str = "done"
    name: str
    times: int
    formal: bool


class StartArguments(Arguments):
    name: Annotated[str, Field(description="Who to greet")]
    times: Annotated[int, Field(description="How many greetings to print")] = 2
    formal: Annotated[bool, Field(description="Whether to greet formally")] = False


class Greet(Workflow):
    @event(source=None, dest="done")
    def start(self, args: StartArguments) -> Inputs:
        name = args.name
        times = args.times
        formal = args.formal
        return Inputs(name=name, times=times, formal=formal)

    @phase("done", final=True)
    def prompt(self, data: Inputs) -> Instruction:
        values = data.model_dump(mode="json", exclude={"phase"})
        return Instruction(render(self.markdown, values), context=values)


WORKFLOW = Greet
