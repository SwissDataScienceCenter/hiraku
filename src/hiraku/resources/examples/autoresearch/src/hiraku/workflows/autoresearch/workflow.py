"""Return the autoresearch procedure as a single prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.markdown import Markdown


class AutoresearchState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final: str = ""


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig) -> Any:
    """Build the prompt-only workflow graph."""
    _, body = BaseRecord._parse_markdown(Path(__file__).with_name("workflow.md"))
    prompt = str(Markdown(body)).strip()

    def deliver(_: AutoresearchState) -> dict[str, str]:
        return {"final": prompt}

    builder = StateGraph(AutoresearchState)
    builder.add_node("deliver", deliver)
    builder.add_edge(START, "deliver")
    builder.add_edge("deliver", END)
    return builder.compile(checkpointer=checkpointer)
