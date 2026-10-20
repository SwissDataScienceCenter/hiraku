# Workflows API

The public API is re-exported from `hiraku.workflows`. Models, graph declarations,
execution, and stored sessions are described in the [Workflows guide](../../guides/workflows.md).

~~~python
from hiraku.workflows import (
    EventArguments, Event, EventResult, Instruction, MarkdownWorkflow, Node,
    Session, State, Workflow, WorkflowGraph, event, node, start,
    list_sessions, list_workflows, load_session, load_workflow,
)
~~~

`load_workflow(id, config=...)` and `list_workflows(config=...)` bind loaded
definitions to the selected project's configuration file. `Workflow.config`
reloads that file on access. `load_session` and `list_sessions` follow the same
project lookup convention as records and contexts.

`Session.state` is a `State` that validates `node`
and preserves workflow-specific fields. Read the position as `session.state.node`.
Loading and cancelling a session do not require its workflow code.
To validate the remaining fields for execution,
call `session.validate_workflow(workflow)`: it returns an instance of the concrete
`workflow.graph.state_model`, detached from the saved data. It checks workflow
ownership, model validation, the current node, and pending-event compatibility
without executing handlers or changing the snapshot. Incompatible models raise
Pydantic validation errors; ownership and graph mismatches raise `WorkflowError`.
Validation uses the workflow definition available now, not its historical model.
`State` allows extra fields, including in authored subclasses. Declared fields
are still validated. A subclass can opt into rejecting unknown fields with
`model_config = ConfigDict(extra="forbid")`.
State is mutable: event handlers may assign fields or update collections and
return the same instance. Assignments are checked when the returned state is
revalidated, rather than individually on assignment.

Dependencies run from `session.py` to `workflow.py` to `graph.py`.
`workflow.py` owns the definition and `Instruction`; `session.py` owns execution,
`EventResult`, and the stored session models. Workflow never imports Session.

`workflow.run(event, arguments, state=None)` executes one step without creating
or saving a session. Supply an `Event` from its graph, an already validated
`EventArguments` instance, and the concrete validated state for ordinary events;
omit state for start. It validates returned states and transitions, builds the
instruction with accepted-event schemas, and returns `(state, instruction)`.
Errors propagate to the caller. Session uses this method and converts failures
into `EventResult` outcomes while handling correlation, history, and persistence.
Direct calls may mutate the supplied state even on failure. Session supplies
detached working state, so failures leave its saved snapshot unchanged.
Instruction handlers receive a deep copy of the next state.

`Session.run_start(workflow, arguments=None)` returns an `EventResult`, including
when start fails before a session exists. `session.run_event(workflow,
instruction_id, event, arguments=None)` reloads the saved session under its lock,
validates once, and passes that state to the event handler. The calling session
remains a snapshot; reload it to read updated state or instructions.
`session.get_instruction(workflow)` reads and checks the snapshot's assignment.
Session inspection and instruction reads use the same compatibility check.
Returned event states are still validated separately before saving.

A loaded workflow owns its graph. `Workflow.build_graph()` defaults to
`WorkflowGraph.from_class(type(self))`, followed by Workflow-specific checks.
The compiler accepts any class with decorated instance methods. Start defines
the `State` subclass; ordinary events accept and return that same model.
`@node` handlers accept the model and return any Pydantic model. No `Workflow`
inheritance is needed. `State` belongs to the graph abstraction; its `node`
field selects the current node, while other fields hold the complete runtime
data. `Workflow.build_graph()` additionally requires exact `Instruction` return
annotations for node handlers and rejects reserved Workflow handler names.

Subclasses can construct graphs explicitly with a `state_model`, a `nodes`
mapping of labels to immutable `Node` definitions, and an `events` mapping of
names to `Event` definitions. `Node(name, final, handler)` groups a graph
position's label, terminal flag, and unbound output handler.
`graph.nodes["done"].handler` retrieves its handler, and
`graph.nodes["done"].final` checks whether it is terminal. `State` is the
evolving runtime data; `Node` is its position in the graph.
Direct construction trusts valid model classes and synchronous handlers.
Construction checks graph structure and freezes copied mappings.
Unreachable nodes are allowed; use
`workflow.graph.check_reachability()` for an optional check that raises
`WorkflowError`. Without a Python sidecar, loading selects `MarkdownWorkflow`,
which infers inputs from scalar YAML defaults and retains the optional
`description` field.

An explicit `build_graph()` override is trusted to supply the Workflow-specific
contracts. All execution paths still validate actual returned states and
instructions. Graph compilation and topology live entirely in `graph.py`, which
has no dependency on the workflow implementation.

`graph.check_transition(event, next_state)` checks that the result has exactly
the graph's state model and its `node` is one of the event's destinations.
Session execution uses this check before persistence; it separately validates
serialized state data and checks instruction outputs. Stored state and session
inspection use `node` for the current position. Session format version 2 rejects
older files; start a new session rather than migrating the old `phase` field.

Python events declare inputs as annotated keyword-only parameters, after the
state parameter for ordinary events. The method named `start` uses
`@start(dest=...)`; ordinary events use `@event(source=..., dest=...)`.
Graph construction generates an `EventArguments` model per event.
`EventArguments.from_function(function)` builds that model class from the
function's keyword-only parameters. It resolves annotations and preserves
defaults and metadata; positional and variadic parameters are ignored by the
factory. `WorkflowGraph.from_class()` separately validates the complete handler
signature, including rejection of positional external inputs and variadics.
`from_text()` then creates a validated instance of the generated model.
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

::: hiraku.workflows.graph.State

::: hiraku.workflows.workflow.Instruction

::: hiraku.workflows.session.EventResult

::: hiraku.workflows.workflow.Workflow

::: hiraku.workflows.markdown.MarkdownWorkflow

::: hiraku.workflows.graph.node

::: hiraku.workflows.graph.start

::: hiraku.workflows.graph.event

::: hiraku.workflows.graph.WorkflowGraph

::: hiraku.workflows.graph.Node


::: hiraku.workflows.graph.Event

::: hiraku.workflows.session.Session

::: hiraku.workflows.session.load_session

::: hiraku.workflows.session.list_sessions

::: hiraku.workflows.workflow.load_workflow

::: hiraku.workflows.workflow.list_workflows

::: hiraku.errors.EventRejected

::: hiraku.errors.NotReady
