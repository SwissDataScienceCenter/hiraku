# Workflows API

The public API is re-exported from `hiraku.workflows`. Models, graph declarations,
execution, and stored sessions are described in the [Workflows guide](../../guides/workflows.md).

~~~python
from hiraku.workflows import (
    Arguments, EventDefinition, EventResult, Instruction, MarkdownWorkflow,
    PhaseDefinition,
    Session, State, Workflow, WorkflowGraph, event, phase,
    list_sessions, list_workflows, load_session, load_workflow,
)
~~~

`load_workflow(id, config=...)` and `list_workflows(config=...)` bind loaded
definitions to the selected project's configuration file. `Workflow.config`
reloads that file on access. `load_session` and `list_sessions` follow the same
project lookup convention as records and contexts.

A loaded workflow owns its graph. `Workflow.build_graph()` defaults to
`WorkflowGraph.from_decorated_methods()`; subclasses can build explicit graphs
using `PhaseDefinition` and `EventDefinition` with unbound `handler`
methods. Graph construction validates and freezes both forms. Without a Python
sidecar, loading selects `MarkdownWorkflow`, which infers inputs from scalar
YAML defaults and retains the optional `description` field.

::: hiraku.workflows.workflow.Arguments

::: hiraku.workflows.workflow.State

::: hiraku.workflows.workflow.Instruction

::: hiraku.workflows.workflow.EventResult

::: hiraku.workflows.workflow.Workflow

::: hiraku.workflows.markdown.MarkdownWorkflow

::: hiraku.workflows.graph.phase

::: hiraku.workflows.graph.event

::: hiraku.workflows.graph.WorkflowGraph

::: hiraku.workflows.graph.PhaseDefinition

::: hiraku.workflows.graph.EventDefinition

::: hiraku.workflows.session.Session

::: hiraku.workflows.session.load_session

::: hiraku.workflows.session.list_sessions

::: hiraku.workflows.workflow.load_workflow

::: hiraku.workflows.workflow.list_workflows

::: hiraku.errors.EventRejected

::: hiraku.errors.NotReady
