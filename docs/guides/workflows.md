# Workflows

Hiraku runs project-defined workflows with LangGraph. Each workflow lives in
`hiraku/workflows/<id>/` and contains a `workflow.py` graph definition plus an
optional `workflow.md` file for editable instructions, metadata, and defaults.
There is no Hiraku-specific graph schema or separate session engine.

## Run a workflow

List available graphs and start one:

```bash
hiraku workflow list
hiraku workflow graph count-steps
hiraku workflow start count-steps --arguments-file - <<'JSON'
{"count": 3}
JSON
```

`workflow graph` prints LangGraph's Mermaid view of the executable topology.
It shows implementation nodes and edges, including loops and conditional
routes.

The response includes a `workflow_session_id`. This is the LangGraph `thread_id` used to
identify the checkpointed run. Inspect it without advancing:

```bash
hiraku workflow status <workflow-session-id>
```

LangGraph checkpoints are stored in
`.hiraku/langgraph-checkpoints.sqlite` at the project root. The returned
`workflow_session_id` is the LangGraph `thread_id`, and the checkpoint ID
identifies the exact state to resume.

If the graph pauses with an interrupt, send its structured response to resume:

```bash
hiraku workflow resume <workflow-session-id> --checkpoint-id <checkpoint-id> --arguments-file - <<'JSON'
{"token": "...", "decision": "continue"}
JSON
```

Use the `checkpoint_id` returned by start or status. A stale checkpoint is
rejected, so retrying an earlier response cannot answer a later interrupt.
Resumes of the same session are serialized across local processes.

The same operations are available through MCP as `workflow_list`,
`workflow_start`, `workflow_status`, and `workflow_resume`. The MCP tools pass
workflow inputs and interrupt responses to LangGraph; the graph owns transitions
and the SQLite checkpointer owns resumable state. A run can be resumed after
restarting Hiraku as long as its project and checkpoint database remain
available.

Each workflow must expose an `input_schema()` function from `workflow.py`;
`workflow_list` includes its JSON Schema as `input_schema`. Interrupts include a
JSON Schema in `response_schema`, and may include `required_capabilities` to
describe what the agent needs for that step. The graph remains responsible for
validating inputs and interrupt responses. Capability names describe needs, not
specific tools; an executor maps them to its own tools. The bundled workflows
demonstrate these contracts with Pydantic models.

The collaborative workflow requires an explicit `task` record ID in its
arguments.

Workflows can declare an `approval` object in an interrupt payload. The MCP
`workflow_approval` tool presents its message and a proposed response and
gathers the standard approve, revise, or stop decision without depending on a
particular workflow or step name. Supply the workflow session ID and current
interrupt ID, then pass the result to `workflow_resume`. The graph checks the
decision before processing the workflow response. This is a cooperative
workflow convention:
callers can submit decisions directly and must be trusted. MCP elicitation does
not restrict other tools or local shell access.

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

import frontmatter
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict

from hiraku.project import Project
from hiraku.markdown import Markdown, render


class GreetingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = "World"


class State(TypedDict, total=False):
    arguments: dict[str, Any]
    final: str


def input_schema() -> dict[str, Any]:
    """Declare the JSON Schema available before workflow_start."""
    return GreetingInput.model_json_schema()


def build_graph(checkpointer: BaseCheckpointSaver[Any], *, project: Project):
    path = Path(__file__).with_name("workflow.md")
    _, body = frontmatter.parse(path.read_text(encoding="utf-8"))
    prompt = Markdown(body)

    def greet(state: State) -> dict[str, str]:
        arguments = GreetingInput.model_validate(state.get("arguments", {}))
        return {"final": render(prompt, arguments.model_dump())}

    builder = StateGraph(State)
    builder.add_node("greet", greet)
    builder.add_edge(START, "greet")
    builder.add_edge("greet", END)
    return builder.compile(checkpointer=checkpointer)
```

The factory receives the configured checkpoint saver and Hiraku project.
LangGraph owns the state schema, nodes, routing, interrupts, and resume behavior.
Use `interrupt(payload)` for a handoff and `Command(resume=response)` to continue
the same checkpointed thread. Return the final user-facing result in the `final`
state key.

Use standard LangGraph construction directly when a workflow needs branching,
loops, parallel work, or human approval. Hiraku does not add decorators or a
second graph-definition layer. Workflows can call Hiraku's research APIs for
records, launches, tracked runs, and provenance from their nodes.

## State and persistence

The workflow session ID maps to LangGraph's `thread_id`; checkpoints are stored in
`.hiraku/langgraph-checkpoints.sqlite` at the project root. The project root's
`.gitignore` should ignore `/.hiraku/`. Use the same workflow session ID for
status and resume.
Checkpoint state is execution state, while Hiraku research records and tracked
runs remain the durable research history.

LangGraph keeps completed threads in its checkpointer. Hiraku does not delete
the checkpoint after completion. Backups and cleanup should be handled at the
project state-directory level.

For a complete graph example, see [workflow examples](../examples/workflows/).
