# Workflows

A workflow is a project-defined Python state machine that gives an agent one
instruction at a time. Its directory contains
`hiraku/workflows/<id>/workflow.md`. Add a `workflow.py` sidecar for a Python
state machine, or use front-matter argument defaults for a Markdown-only prompt.

A workflow tracks a typed **state**, whose `phase` identifies a node in its
declared graph. Events validate submitted evidence and return a complete next
state. Phase methods turn that state into an instruction.

## Run a workflow

`hiraku init` installs collaborative research. Two examples add other workflows:

- `hiraku init --example workflow-examples` adds count-steps, greet, and scheduler.
- The autoresearch example adds a single-instruction research procedure.

Over MCP, start with the tool or prompt named after the workflow. The start
wrapper runs the entry event and delivers the first instruction. An instruction
contains text, context, and an events block with accepted event schemas and the
session/instruction IDs. Carry out the text and send an event through
`workflow_send_event`. The conversational MCP wrapper returns the submission
outcome and, after acceptance or a stale submission, the saved current instruction.

Approval requested in workflow text is a conversational contract. Hiraku does
not authenticate human approval.

Resume with `session_list` and `session_show`, rather than starting another
session. `session_cancel` stops future submissions without reverting artifacts.

### Terminal driver

~~~bash
uv run hiraku-agent count-steps --count 3 --interactive
uv run hiraku-agent count-steps step --interactive
uv run hiraku-agent count-steps finish --interactive
~~~

`hiraku-agent WORKFLOW` starts or resumes an active session. Use `--new` to start
another or `--session ID` to select one. Event subcommands use argument-model
fields as options. Strings are literal text; other values are JSON.
The CLI and MCP prompt adapters use `EventArguments.from_text(values)` to decode
these string inputs and validate the model. Regular JSON tools and Python calls
use normal model validation.

The parent reads one saved instruction and starts a worker. The worker gets a
dedicated stdio MCP connection exposing only `workflow_send_event` for that
session and instruction. It submits directly, receives only an event result,
and stops. The parent retrieves and dispatches the next instruction.

The default worker is Claude Code, configured with the generated MCP file and
the submission tool. Override headless execution with `HIRAKU_AGENT_CMD` or
`--agent-cmd`, and interactive execution with `HIRAKU_AGENT_INTERACTIVE_CMD`.
Custom commands must configure their MCP client using the JSON file named by
`HIRAKU_MCP_CONFIG`. The file contains a standard `mcpServers` mapping with the
stdio server command and arguments. There is no event-file fallback.

Workers receive `HIRAKU_PROMPT_FILE`, `HIRAKU_INSTRUCTION_FILE`, and
`HIRAKU_SESSION_ID`. Final assignments have no submission connection or
`HIRAKU_MCP_CONFIG`. Prompt, instruction, configuration, output, and submission
journal files are stored beneath the session's instruction/attempt directory.

A worker can correct rejected input up to three times within its conversation.
A not-ready submission pauses execution (exit 4); resuming retries the saved
submission before starting another worker. Interactive workers that finish
without submitting pause execution (exit 3); headless workers that do so fail.
Subprocess failures propagate, including for final instructions. Accepted events
remain saved even if their worker subsequently exits unsuccessfully.

`--interactive` asks the worker to explain its intended work and wait for your
go-ahead. Exit without submitting to pause. Submission-only tool exposure defines
the worker interface; workers with shell access are not sandboxed from other
project commands.

## Author a workflow

~~~python
from typing import Annotated, Literal

from pydantic import Field

from hiraku.workflows import (
    Instruction, State, Workflow, event, phase, start,
)


class Counter(State):
    phase: Literal["counting", "done"] = "counting"
    count: int
    step: int = 0


class Count(Workflow):
    @start(dest="counting")
    def start(
        self, *, count: Annotated[int, "Number of steps.", Field(gt=0)] = 3
    ) -> Counter:
        return Counter(count=count)

    @phase("counting")
    def counting(self, state: Counter) -> Instruction:
        return Instruction(
            self.markdown.section("## Count"),
            context={"next": state.step + 1},
        )

    @event(source="counting", dest=["counting", "done"])
    def counted(self, state: Counter) -> Counter:
        step = state.step + 1
        return state.model_copy(update={
            "step": step,
            "phase": "done" if step >= state.count else "counting",
        })

    @phase("done", final=True)
    def done(self, state: Counter) -> Instruction:
        return Instruction("Report the count.", context={"count": state.step})


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

### Models, phases, and events

- Event inputs are annotated keyword-only parameters. Hiraku generates an
  `EventArguments` model from each signature and rejects unknown fields. Use Python
  defaults for optional inputs and `Annotated[T, "Description"]` for input
  descriptions. Add `Field(...)` metadata only when constraints are needed,
  e.g. `Annotated[int, "Number of steps.", Field(gt=0)]`. Each parameter accepts
  at most one description string; it takes precedence over a `Field` description.
  Other metadata passes through to Pydantic unchanged.
  Nested Pydantic models can define shared input structures
  and validators; cross-field event checks can raise `EventRejected`.
- `State` is the complete persisted state, including `phase`. Fields are frozen,
  although nested collections are not deeply frozen. Return a new state with
  `model_copy(update=...)` or construct one. The runtime serializes and validates
  the returned state, including otherwise unvalidated copy updates.
- The method named `start` must use `@start(dest=...)` and return a concrete
  `State` subclass. It has no previous state.
  Every other event takes that same state model, then keyword-only inputs,
  and returns the state model. There are no destination-string or `None` returns.
- `@phase(name, final=False)` registers an instruction method taking only the
  state. Final phases accept no events. Nonfinal phases accept at least one.
  Instruction methods read state; they must not perform transitions.
- `@event(source=..., dest=...)` declares possible graph edges. Sources and
  destinations can each be a name or sequence of names. The returned state's
  phase must be one of the declared destinations. Ordinary events always require
  a source; only `@start` declares an entry edge.
- An event without input parameters accepts only an empty input object. Omitted
  inputs are validated from `{}`; parameters without defaults are required.
- Models are defined at module scope before their workflow. Handlers are
  synchronous instance methods. `self` and the state are positional parameters
  without defaults; external inputs follow `*`. Variadic parameters are rejected.
  Direct Python method calls bypass validation; use `run_start` and `run_event`
  for validated execution and session persistence.
- Graph construction checks phase references and final-phase rules. Unreachable
  phases are allowed; call `workflow.graph.check_reachability()` to reject them
  explicitly. The check raises `WorkflowError` and does not change the graph.
- Raise `EventRejected` for invalid submissions and `NotReady` for valid input
  awaiting evidence. Unexpected failures are execution errors. These outcomes
  do not save session advancement.

Accepted events produce a new instruction revision, even when their phase stays
unchanged. Reads return that stored instruction without invoking any handlers.
Generate tokens and choose assignments in events, not in instruction methods.

A final phase completes the session when its instruction is stored. The driver
still carries out that final instruction. Completion means no more events are
accepted; its context can describe successful, partial, or stopped results.

### Definition settings and project configuration

`Workflow` extends `BaseRecord` with optional `description` and an instance-owned
`graph`. Additional annotated fields are front-matter settings. Use `ClassVar`
for constants. Authors do not override the framework-owned constructor.

`self.markdown` is the document body. Select sections with their exact heading
line, for example `self.markdown.section("## Count")`. Workflow headings must be
unique. Use `hiraku.markdown.render(text, values)` for explicit placeholder
substitution. `Instruction.to_text()` renders the instruction, context, and events.

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

`from_class()` can compile ordinary Python classes with Pydantic state and phase
output models, independently of Workflow. It validates synchronous instance
method signatures and generates event input models. The default
`Workflow.build_graph()` then requires a `State` subclass and `Instruction` phase
return annotations, and rejects handlers that override its infrastructure
methods. These checks run when the workflow is loaded.

Subclasses may override `build_graph()` and construct `WorkflowGraph` directly,
as `MarkdownWorkflow` does. Supply a state model and mappings of phase and event
definitions. Their public `handler` fields hold unbound methods, such as
`type(self).start`; execution supplies the workflow instance. Explicit builders
are trusted to supply the Workflow-specific handler contracts; execution still
validates returned states and instructions.

Both paths share structural graph validation: entry event, names, known phases,
and final/outgoing-event consistency. Definition mappings are copied and frozen.
Unreachable phases are allowed; call `graph.check_reachability()` to reject them.
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

Core execution returns an `EventResult`, containing no instruction:

~~~python
workflow = load_workflow("count", config=config)
started = workflow.run_start({"count": 2})
assignment = workflow.get_instruction(started.session_id)

result = workflow.run_event(
    started.session_id, assignment["instruction_id"], "counted",
)
next_assignment = workflow.get_instruction(started.session_id)
~~~

`EventResult` contains `status`, available `session_id` and `instruction_id`,
and optional `message`. The instruction ID is the current revision after
processing. Serialize with `model_dump(exclude_none=True)` at transport boundaries.

| Status | Meaning |
| --- | --- |
| accepted | Saved advancement, or recognized an identical retry of the most recent accepted event. |
| rejected | Correct the input; the current instruction remains pending. |
| not_ready | Evidence or the session lock is unavailable; retry later. |
| stale | The submission addresses an instruction no longer pending; retrieve current work. |
| error | Execution failed; no advancement was saved. |

Instruction reads contain `session_id`, `instruction_id`, `session_status`,
`phase`, and the saved instruction. Cancelled or incompatible executions return
an explanation without an actionable instruction.

Load/list sessions with module-level `load_session(id, config=...)` and
`list_sessions(config=...)`. The latter returns `Session` objects newest first,
logging and skipping malformed or unsupported files. A session exposes raw state,
derived phase, saved instruction, history, timestamps, and status.
`session.show(workflow)` inspects compatibility; `session.cancel()` does not
require working workflow code. Session persistence retains its file location,
not a configuration object.

~~~bash
uv run hiraku workflow start count --arguments-file inputs.json
uv run hiraku workflow send-event SESSION_ID counted --instruction-id 1
uv run hiraku session show SESSION_ID --json
uv run hiraku session cancel SESSION_ID
~~~

CLI start includes the first instruction. CLI send-event prints only the event
result; use session show to retrieve the next instruction. Conversational MCP
combines those operations; terminal workers receive only the event result.

Session advancement is guarded by a per-session lock and atomic file replacement.
Cancellation takes precedence over retry replay. Handler, instruction, or save
failures preserve the previous saved session. External record writes and launches
are not rolled back; validate before effects and make effects idempotent where
practical.

Sessions use a versioned format. Old-format sessions cannot continue and must be
restarted; their files are left untouched. They are not migrated.

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
placeholders fail without creating a session. Values are also instruction context.
The graph is `start → done`, with `done` final. The session completes when
the instruction is saved; the agent then carries it out. Later reads retrieve
that saved instruction, even if the Markdown or defaults have changed.

An argument named `gate` is an ordinary prompt value; it does not create an
authenticated approval gate. Keep such requests explicit in the prompt.

A present Python sidecar takes precedence. A broken sidecar is an error and never
falls back to Markdown-only behavior. Both forms require `workflow.md` and use
the same config, session, graph, CLI, MCP, and agent execution paths.

## Bundled workflows

Collaborative research runs draft, implement, launch, and analyze phases with
revision events and early completion. It writes experiment records, verifies
commits and run attribution, and records verdicts. Approval is conversational;
use MCP or the interactive driver.

Count-steps checks numbered instructions with random tokens and an explicit user
decision between steps. Greet renders one final prompt. Scheduler selects records
by priority and changes assignments while staying in the working phase.
