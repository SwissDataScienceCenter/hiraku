"""Markdown-only workflows with argument types inferred from YAML defaults."""

from __future__ import annotations

import keyword
from typing import Any

from pydantic import Field, create_model

from ..errors import WorkflowError
from ..markdown import render
from .graph import Event, EventArguments, Phase, WorkflowGraph
from .workflow import Instruction, State, Workflow


class MarkdownState(State):
    phase: str = "done"
    arguments: dict[str, Any]


class MarkdownWorkflow(Workflow):
    """A single final instruction, with argument types inferred from YAML defaults.

    Only string, integer, float, and boolean defaults are supported. The inherited
    description field and other record metadata remain available in front matter.
    """

    arguments: dict[str, Any] = Field(default_factory=dict)

    def build_graph(self) -> WorkflowGraph:
        fields = {}
        for name, default in self.arguments.items():
            if (
                not name.isidentifier()
                or name.startswith("_")
                or keyword.iskeyword(name)
                or hasattr(EventArguments, name)
            ):
                raise WorkflowError(f"Invalid argument name: {name!r}")
            if type(default) not in (str, int, float, bool):
                raise WorkflowError(
                    f"Argument {name!r} needs a string, integer, float, "
                    "or boolean default"
                )
            fields[name] = (type(default), default)
        try:
            inputs = create_model(
                "MarkdownEventArguments", __base__=EventArguments, **fields
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowError(f"Invalid Markdown arguments: {exc}") from exc
        return WorkflowGraph(
            state_model=MarkdownState,
            phases={"done": Phase("done", True, type(self).prompt)},
            events={
                "start": Event(
                    "start",
                    None,
                    ("done",),
                    inputs,
                    self.description or "",
                    type(self).start,
                )
            },
        )

    def start(self, **arguments: Any) -> MarkdownState:
        """Start the Markdown instruction with validated argument values."""
        return MarkdownState(arguments=arguments)

    def prompt(self, state: MarkdownState) -> Instruction:
        """Render the complete Markdown body as the final assignment."""
        return Instruction(
            render(self.markdown, state.arguments), context=state.arguments
        )
