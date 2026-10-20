from hiraku.workflows import Instruction, State, Workflow, event, phase


class PromptState(State):
    phase: str = "done"


class Autoresearch(Workflow):
    @event(source=None, dest="done")
    def start(self) -> PromptState:
        return PromptState()

    @phase("done", final=True)
    def prompt(self, state: PromptState) -> Instruction:
        return Instruction(self.markdown)


WORKFLOW = Autoresearch
