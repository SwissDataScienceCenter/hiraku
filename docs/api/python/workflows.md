# Workflows API

The public API is re-exported from `hiraku.workflows`. Models, graph declarations,
execution, and stored sessions are described in the [Workflows guide](../../guides/workflows.md).

~~~python
from hiraku.workflows import (
    EventArguments, Event, EventResult, Instruction, MarkdownWorkflow,
    Phase,
    Session, State, Workflow, WorkflowGraph, event, phase, start,
    list_sessions, list_workflows, load_session, load_workflow,
)
~~~

`load_workflow(id, config=...)` and `list_workflows(config=...)` bind loaded
definitions to the selected project's configuration file. `Workflow.config`
reloads that file on access. `load_session` and `list_sessions` follow the same
project lookup convention as records and contexts.

A loaded workflow owns its graph. `Workflow.build_graph()` defaults to
`WorkflowGraph.from_class(type(self))`, followed by Workflow-specific checks.
The compiler accepts any class with decorated instance methods: start defines
the Pydantic state model, ordinary events accept and return that same model,
and phases accept it and return a Pydantic model. It does not require `Workflow`
inheritance or a `phase` field on the model. `Workflow.build_graph()` additionally
requires a `State` subclass, exact `Instruction` phase return annotations, and
handler names that do not override Workflow infrastructure.

Subclasses can build explicit graphs using `Phase` and `Event` with unbound
`handler` methods. Direct graph construction is a typed Python API: callers supply valid
model classes and synchronous handlers. Construction freezes the mappings and
checks graph structure. Unreachable phases are allowed; use
`workflow.graph.check_reachability()` for an optional check that raises
`WorkflowError`. Without a Python sidecar, loading selects `MarkdownWorkflow`,
which infers inputs from scalar
YAML defaults and retains the optional `description` field.

An explicit `build_graph()` override is trusted to supply the Workflow-specific
contracts. All execution paths still validate actual returned states and
instructions. Graph compilation and topology live entirely in `graph.py`, which
has no dependency on the workflow implementation.

Python events declare inputs as annotated keyword-only parameters, after the
state parameter for ordinary events. The method named `start` uses
`@start(dest=...)`; ordinary events use `@event(source=..., dest=...)`.
Graph construction generates an `EventArguments` model per event.
Python defaults supply defaults; `Annotated[T, "Description"]`
supplies parameter descriptions. Optional `Field(...)` metadata supplies
constraints. Multiple description strings are rejected; a string description
takes precedence over a `Field` description, and other metadata is preserved.
`Event.arguments` exposes the generated
model for validation and schema inspection. Handlers receive validated keyword
values, including nested model instances.

Every `Event.arguments` is a model, including an empty model for an
event without inputs. Manually constructed definitions must also supply a model.

::: hiraku.workflows.graph.EventArguments

::: hiraku.workflows.workflow.State

::: hiraku.workflows.workflow.Instruction

::: hiraku.workflows.workflow.EventResult

::: hiraku.workflows.workflow.Workflow

::: hiraku.workflows.markdown.MarkdownWorkflow

::: hiraku.workflows.graph.phase

::: hiraku.workflows.graph.start

::: hiraku.workflows.graph.event

::: hiraku.workflows.graph.WorkflowGraph

::: hiraku.workflows.graph.Phase

::: hiraku.workflows.graph.Event

::: hiraku.workflows.session.Session

::: hiraku.workflows.session.load_session

::: hiraku.workflows.session.list_sessions

::: hiraku.workflows.workflow.load_workflow

::: hiraku.workflows.workflow.list_workflows

::: hiraku.errors.EventRejected

::: hiraku.errors.NotReady
