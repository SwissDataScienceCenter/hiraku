# Workflows

A workflow is a project-defined Python state machine that gives an agent one
instruction at a time. Its directory contains
`hiraku/workflows/<id>/workflow.md`. Add a `workflow.py` sidecar for a Python
state machine, or use front-matter argument defaults for a Markdown-only prompt.
Workflow IDs use letters, digits, and hyphens, starting with a letter or digit.
The ID `runs` is reserved for Hiraku's run-query tool.

A workflow tracks a typed **state**, whose `node` identifies a node in its
declared graph. Events validate submitted evidence and return a complete next
state. Methods registered with `@node` turn that data into an instruction.

## Run a workflow

`hiraku init` installs collaborative research. Two examples add other workflows:

- `hiraku example create workflows` adds count-steps, greet, and scheduler.
- The autoresearch example adds a single-instruction research procedure.

Over MCP, start with the tool or prompt named after the workflow. The start
wrapper runs the entry event and delivers the first instruction. An instruction
contains text, inline data, optional research context, and an events block with
accepted event schemas and the session/instruction IDs. Carry out the text and
send an event through
`workflow_send_event`. The conversational MCP wrapper returns the submission
outcome and, after acceptance or a stale submission, the saved current instruction.

Approval requested in workflow text is a conversational contract. Hiraku does
not authenticate human approval.

Resume with `session_list` and `session_show`, rather than starting another
session. `session_cancel` stops future submissions without reverting artifacts.

## Author a workflow

~~~python
from typing import Annotated, Literal

from pydantic import Field

from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow


class Counter(State):
    node: Literal["counting", "done"] = "counting"
    count: int
    step: int = 0


class Count(Workflow):
    @start(dest="counting")
    def start(
        self, *, count: Annotated[int, "Number of steps.", Field(gt=0)] = 3
    ) -> Counter:
        return Counter(count=count)

    @node("counting")
    def counting(self, state: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Count"),
            data={"next": state.step + 1},
        )

    @event(source="counting", dest=["counting", "done"])
    def counted(self, state: Counter) -> Counter:
        state.step += 1
        state.node = "done" if state.step >= state.count else "counting"
        return state

    @node("done", final=True)
    def done(self, state: Counter) -> Instruction:
        return Instruction("Report the count.", data={"count": state.step})


WORKFLOW = Count
~~~

Its `workflow.md` supplies settings and prompts:

~~~markdown
---
id: count
description: Count a requested number of steps.
---
# Count

## Count

Print the next number, then submit counted.
~~~

### Models, nodes, and events

- Event inputs are annotated keyword-only parameters. Hiraku generates an
  `EventArguments` model from each signature and rejects unknown fields. Use Python
  defaults for optional inputs and `Annotated[T, "Description"]` for input
  descriptions. Add `Field(...)` metadata only when constraints are needed,
  e.g. `Annotated[int, "Number of steps.", Field(gt=0)]`. Each parameter accepts
  at most one description string; it takes precedence over a `Field` description.
  Other metadata passes through to Pydantic unchanged.
  Nested Pydantic models can define shared input structures
  and validators; cross-field event checks can raise `EventRejected`.
- `State` is the graph's complete runtime data, including the current `node` label.
  Workflow sessions persist it. Event handlers can update fields and nested
  collections in place and return the state, or construct a new state.
  The runtime serializes and validates the returned state before saving;
  assignments are not validated individually.
- The method named `start` must use `@start(dest=...)` and return a concrete
  `State` subclass. It has no previous state.
  Every other event takes that same state model, then keyword-only inputs,
  and returns the state model. There are no destination-string or `None` returns.
- `@node(name, final=False)` registers an instruction method taking only the
  state. Final nodes accept no events. Nonfinal nodes accept at least one.
  Instruction methods read state; they must not perform transitions.
- `@event(source=..., dest=...)` declares possible graph edges. Sources and
  destinations can each be a name or sequence of names. The returned state's
  node must be one of the declared destinations. Ordinary events always require
  a source; only `@start` declares an entry edge.
- An event without input parameters accepts only an empty input object. Omitted
  inputs are validated from `{}`; parameters without defaults are required.
- Models are defined at module scope before their workflow. Handlers are
  synchronous instance methods. `self` and the state are positional parameters
  without defaults; external inputs follow `*`. Variadic parameters are rejected.
  Direct Python method calls bypass validation; use `Session.run_start` and
  `session.run_event`
  for validated execution and session persistence.
- Graph construction checks node references and final-node rules. Unreachable
  nodes are allowed; call `workflow.graph.check_reachability()` to reject them
  explicitly. The check raises `WorkflowError` and does not change the graph.
- Raise `EventRejected` for invalid submissions and `NotReady` for valid input
  awaiting evidence. Unexpected failures are execution errors. These outcomes
  do not save session advancement.

Accepted events produce a new instruction revision, even when their node stays
unchanged. Reads return that stored instruction without invoking any handlers.
Generate tokens and choose assignments in events, not in instruction methods.

A final node completes the session when its instruction is stored. The agent
still carries out that final instruction. Completion means no more events are
accepted; its data can describe successful, partial, or stopped results.

### Definition settings and project configuration

`Workflow` extends `BaseRecord` with optional `description` and an instance-owned
`graph`. Additional annotated fields are front-matter settings. Use `ClassVar`
for constants. Authors do not override the framework-owned constructor.

`self.markdown` is the document body. Select sections with their exact heading
line, for example `self.markdown.section("## Count")`. Workflow headings must be
unique. Use `hiraku.markdown.render(text, values)` for explicit placeholder
substitution. `Instruction.to_text()` renders the instruction, data, context
references, and events.

### Attaching research context

Use `data` for inline values and `context` for project-local research references:

```python
Instruction(
    "Evaluate the experiment.",
    data={"metric": "accuracy"},
    context={
        "records": ["evaluation-protocol", "experiment-42"],
    },
)
```

The record list is optional. Unknown keys and non-string IDs are rejected.
References are resolved against the workflow's project before session progress is
saved; missing references fail the step. Duplicate IDs are removed in order.
Sessions store IDs rather than content snapshots. Reading a reference later sees
the current contents, including edits since the instruction was issued.

`Instruction.context` previously held arbitrary inline values; move those values
to `Instruction.data`. Existing saved instructions using the previous format are
not migrated. `data` retains the existing JSON serialization behavior and can be
any JSON-serializable value.

`load_workflow(id, config=...)` binds the instance to that configuration file's
absolute path. Direct construction also accepts `config=...`; omission discovers
the project. Every `self.config` access reloads the same file, so saved settings
and active-record changes are visible without switching projects when the working
directory changes. Unsaved modifications to a previously supplied config object
are not retained. Missing or invalid config files raise normal loading errors.

Inside a handler, use `config = self.config` when several operations should share
one snapshot, then pass it to record/run APIs. Config is private execution context,
not a Markdown field. Workflow execution methods do not take `config=`.

### Graph construction

Each loaded workflow instance owns an immutable `workflow.graph`, built once
during construction. It is the source for dispatch, accepted events, schemas,
and Mermaid diagrams. The default `build_graph()` calls
`WorkflowGraph.from_class(type(self))`; inherited declarations are
collected in method-resolution order, honoring overrides. There is no class-level
execution graph.

`from_class()` compiles ordinary Python classes with a `State` subclass and
Pydantic output models, independently of Workflow. The graph owns `State`,
handler signature validation, event input models, and transition rules. The
default `Workflow.build_graph()` requires `Instruction` node-handler return
annotations and rejects handlers that override its infrastructure methods.
These checks run when the workflow is loaded.

Subclasses may override `build_graph()` and construct `WorkflowGraph` directly,
as `MarkdownWorkflow` does. Supply a state model, a `nodes` mapping of labels to
immutable `Node(name, final, handler)` definitions, and an `events` mapping of
names to `Event` definitions. `Node` describes a graph position; `State` holds
the complete evolving data, with `State.node` selecting that position. Access
the output handler through `graph.nodes[label].handler` and finality through
`graph.nodes[label].final`. Execution supplies the workflow instance to unbound
handlers. Explicit builders
are trusted to supply the Workflow-specific handler contracts; execution still
validates returned states and instructions.

Both paths share structural graph validation: entry event, names, known labels,
and final/outgoing-event consistency. Mappings and node definitions are immutable.
Finality is explicit on each node rather than inferred from
missing event declarations. `graph.check_transition(event, next_state)` checks
the returned state model and declared destinations before persistence.
Unreachable nodes are allowed; call `graph.check_reachability()` to reject them.
Invalid structural definitions fail when constructed or loaded. Multiple start
destinations are supported. Graph edges describe possible paths, not proof that
Python conditions will take every path.

~~~bash
uv run hiraku workflow graph count
uv run hiraku workflow graph count --session SESSION_ID
~~~

Python exposes `workflow.graph.to_mermaid(current=...)` and MCP exposes
`hiraku://workflows/<id>/graph`. Workflow code is trusted project Python.
MCP tool definitions are fixed when the server starts; reconnect after editing.

## Execution results and sessions

Starting returns the saved `Session`; subsequent events return an `EventResult`:

~~~python
from hiraku.workflows.session import Session, load_session
from hiraku.workflows.workflow import load_workflow

workflow = load_workflow("count", config=config)
session = Session.run_start(workflow, {"count": 2})
assignment = session.get_instruction(workflow)

result = session.run_event(
    workflow, assignment["instruction_id"], "counted",
)
session = load_session(session.id, config=config)
next_assignment = session.get_instruction(workflow)
~~~

Workflow defines handlers and instructions; Session executes them and owns saved
progress. Session calls `workflow.run(event, arguments, state=None)` to invoke
handlers, validate their outputs and transitions, and build the next instruction.
This lower-level method expects validated inputs and returns `(state, instruction)`;
it neither saves sessions nor catches execution errors. Event handlers can mutate
the supplied state, including before raising an error. Instruction handlers
receive a separate deep copy so they cannot change the result's state.
Loaded sessions are snapshots. Submission reloads current state under
the session lock without changing the caller's snapshot; reload to inspect the
new state. Neither object caches the other.
Session execution uses a detached state; rejected, not-ready, or failed steps
leave saved progress unchanged even if an event has mutated its working state.

Start failures raise: invalid arguments raise `EventRejected`, readiness failures
raise `NotReady`, and other execution or persistence errors propagate. Failed
starts create no session. CLI starts report errors and MCP starts return tool
errors; prompt starts explain failures in text.

`EventResult` contains `status`, available `instruction_id`, and optional `message`.
The instruction ID is the current revision after processing. Session identity
comes from `Session.id`; CLI and MCP responses add `session_id` at the transport
boundary. Serialize results with `model_dump(exclude_none=True)`.

| Status | Meaning |
| --- | --- |
| accepted | Saved advancement to the next instruction. |
| rejected | Correct the input; the current instruction remains pending. |
| not_ready | Evidence or the session lock is unavailable; retry later. |
| stale | The submission addresses an instruction no longer pending; retrieve current work. |
| error | Execution failed; no advancement was saved. |

Instruction reads contain `session_id`, `instruction_id`, `session_status`,
`node`, and the saved instruction. Cancelled or incompatible executions return
an explanation without an actionable instruction.

Load/list sessions with module-level `load_session(id, config=...)` and
`list_sessions(config=...)`. The latter returns `Session` objects newest first,
logging and skipping malformed files. A session exposes a typed state
snapshot, saved instruction, history, timestamps, and status. The current position
is stored under `state.node` and exposed as `node` in inspection and listing
responses.
`session.show(workflow)` inspects compatibility; `session.cancel()` does not
require working workflow code. Session persistence retains its file location,
not a configuration object.
Inspection omits instructions for active sessions whose workflow is missing or
incompatible, while retaining state, history, and an explanation.

Session execution records one history entry per event attempt, including start,
with its event and `result`, through Python, CLI, and MCP. Reads and
cancellation do not add history entries. An `accepted` event advances `instruction_id`;
failed events and resubmissions do not. A submission for an
instruction that is no longer current returns `stale`, even when it exactly
matches an earlier accepted event. MCP includes the current instruction and
revision so the client can continue from there.
Accepted entries retain validated event arguments; other entries do not copy
request or response bodies. Saving an event attempt updates `updated_at`.
Revision tracking is independent of history length.
Each entry records the `node` before the attempt; start entries use `null`.
Stale submissions record the current stored node.
The stored `result` retains status, message, and the returned revision;
the entry's `instruction_id` identifies the submitted revision.

Each entry has an optional `client_info` object containing `client`,
`client_session_id`, and `client_turn_id`; these are not top-level entry fields.
Project-local client hooks installed by `hiraku init` supply these identifiers
for workflow start and send-event tools. Missing
identifiers remain `null`; an unattributed entry has `client_info: null`.
Prompt starts retain their initial event without hook attribution.
Failed starts have no session history to append to. Inspect the JSON session
files or use `hiraku session show SESSION_ID --json` to read the history.

Python callers may pass `client_info=ClientInfo(...)` to `Session.run_start` and
`Session.run_event`, importing `ClientInfo` from
`hiraku.workflows.session`.
This metadata is separate from workflow arguments and is not passed to handlers.
Start and event execution share argument validation, handler execution, output
validation, and persistence. Accepted state and history are saved together;
failed event outcomes are recorded under the same session lock without advancing.

Session loading uses `State`, which validates the
string `node` and preserves all other saved fields. Read the current position
through `session.state.node`. State models allow extra fields by default;
authored subclasses can reject them with `model_config = ConfigDict(extra="forbid")`.
The workflow interprets that snapshot through
`state = session.validate_workflow(workflow)`, which returns its concrete `State`
model after checking ownership, node, and pending-event compatibility. The result
is detached from the stored data; validation neither executes handlers nor saves
changes. This check uses the current workflow definition. Event execution reuses
the validated state; it validates the newly returned state separately before
persistence. Inspection and cancellation remain available if workflow code is
missing or incompatible.

~~~bash
uv run hiraku workflow start count --arguments-file inputs.json
uv run hiraku workflow send-event SESSION_ID counted --instruction-id 1
uv run hiraku session show SESSION_ID --json
uv run hiraku session cancel SESSION_ID
~~~

CLI start includes the first instruction. CLI send-event prints only the event
result; use session show to retrieve the next instruction. Conversational MCP
combines those operations.

Session advancement is guarded by a per-session lock and atomic file replacement.
Cancellation takes precedence over stale-instruction checks. Handler, instruction,
or save failures preserve the previously saved workflow state. External record
writes and launches are not rolled back; validate before effects and make effects
idempotent where practical.

## Markdown-only workflows

For one final instruction, create only a workflow.md file:

~~~yaml
---
id: research
description: Run a bounded research task.
arguments:
  num_iterations: 5
  max_val: 0.5
  gate: true
---
Run {{num_iterations}} iterations, targeting {{max_val}}.
Ask for approval: {{gate}}.
~~~

The `description` field is optional and has the same meaning as in Python
workflows: it describes the workflow in listings, tools, and prompts.

`MarkdownWorkflow` infers each argument's type from its YAML default:
strings, integers, floats, and booleans. Every argument has a default and can be
overridden. Omitted `arguments` means no inputs. Null, collection defaults, and
argument names that are not public Python identifiers are rejected. Use a Python
workflow for required inputs, complex types, or additional validation.

The complete body is rendered with the validated values using
`hiraku.markdown.render`. Booleans render as `true`/`false`; missing
placeholders fail without creating a session. Values are also instruction data.
The graph is `start → done`, with `done` final. The session completes when
the instruction is saved; the agent then carries it out. Later reads retrieve
that saved instruction, even if the Markdown or defaults have changed.

An argument named `gate` is an ordinary prompt value; it does not create an
authenticated approval gate. Keep such requests explicit in the prompt.

A present Python sidecar takes precedence. A broken sidecar is an error and never
falls back to Markdown-only behavior. Both forms require `workflow.md` and use
the same config, session, graph, CLI, and MCP execution paths.

## Bundled workflows

Collaborative research runs draft, implement, launch, and analyze nodes with
revision events and early completion. It writes experiment records, verifies
commits and run attribution, and records verdicts. Approval is conversational;
use MCP.
Launch validation and analysis use only runs attributed to the requested
entrypoint; runs with missing entrypoint attribution are ignored. Generated
launch commands quote their arguments for the shell.

Count-steps checks numbered instructions with random tokens and an explicit user
decision between steps. Greet renders one final prompt. Scheduler selects records
by priority and changes assignments while staying in the working node.
