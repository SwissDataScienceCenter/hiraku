# Workflows

Hiraku runs project-defined workflows with LangGraph. Each workflow lives in
`hiraku/workflows/<id>/` and contains a `workflow.py` graph definition plus an
optional `workflow.md` file for editable instructions, metadata, and defaults.
There is no Hiraku-specific graph schema or separate session engine.

## Run a workflow

List available graphs and start one:

```bash
hiraku workflow list
hiraku workflow start count-steps --arguments-file - <<'JSON'
{"count": 3}
JSON
```

The response includes a `run_id`. This is the LangGraph `thread_id` used to
identify the checkpointed run. Inspect it without advancing:

```bash
hiraku workflow status <run-id>
```

If the graph pauses with an interrupt, send its structured response to resume:

```bash
hiraku workflow resume <run-id> --arguments-file - <<'JSON'
{"token": "...", "decision": "continue"}
JSON
```

The same operations are available through MCP as `workflow_list`,
`workflow_start`, `workflow_status`, and `workflow_resume`. The MCP tools pass
workflow inputs and interrupt responses to LangGraph; the graph owns transitions
and the SQLite checkpointer owns resumable state. A run can be resumed after
restarting Hiraku as long as its project and checkpoint database remain
available.

## Define a workflow

Create `hiraku/workflows/greet/workflow.md`:

```markdown
---
id: greet
description: Greet a person.
arguments:
  name: World
---
# Greeting

Hello, {{name}}!
```

Then define a normal LangGraph graph in the sibling `workflow.py`:

```python
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from hiraku.base import BaseRecord
from hiraku.config import HirakuConfig
from hiraku.markdown import Markdown, render


class State(TypedDict, total=False):
    arguments: dict[str, Any]
    final: str


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, config: HirakuConfig):
    _, body = BaseRecord._parse_markdown(Path(__file__).with_name("workflow.md"))
    prompt = Markdown(body)

    def greet(state: State) -> dict[str, str]:
        return {"final": render(prompt, state.get("arguments", {}))}

    builder = StateGraph(State)
    builder.add_node("greet", greet)
    builder.add_edge(START, "greet")
    builder.add_edge("greet", END)
    return builder.compile(checkpointer=checkpointer)
```

The factory receives the configured checkpoint saver and Hiraku project config.
LangGraph owns the state schema, nodes, routing, interrupts, and resume behavior.
Use `interrupt(payload)` for a handoff and `Command(resume=response)` to continue
the same checkpointed thread. Return the final user-facing result in the `final`
state key.

Use standard LangGraph construction directly when a workflow needs branching,
loops, parallel work, or human approval. Hiraku does not add decorators or a
second graph-definition layer. Workflows can call Hiraku's research APIs for
records, launches, tracked runs, and provenance from their nodes.

## State and persistence

The run ID maps to LangGraph's `thread_id`; checkpoints are stored in
`.hiraku/langgraph-checkpoints.sqlite`. Use the same run ID for status and resume.
Checkpoint state is execution state, while Hiraku research records and tracked
runs remain the durable research history.

LangGraph keeps completed threads in its checkpointer. Hiraku does not delete
the checkpoint after completion. Backups and cleanup should be handled at the
project state-directory level.

For a complete graph example, see [workflow examples](../examples/workflows/).
