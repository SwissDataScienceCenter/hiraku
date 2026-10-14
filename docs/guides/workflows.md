# Workflows

Workflows turn a research procedure into a sequence of explicit steps that your
coding agent carries out in your normal conversation. Python code in your
project decides what happens next and checks what the agent reports, so
budgets, candidate lists, launch approvals, and evidence are enforced instead
of left to the agent's memory.

A workflow consists of three concepts:

- **Workflow**: a directory `hiraku/workflows/<workflow-id>/`. `workflow.md`
  is the workflow's record: front matter with its `id` and settings, and a body
  with its prompts. `workflow.py` defines its class, which adds the operations.
- **Session**: one invocation of a workflow, stored in
  `hiraku/sessions/<session-id>.json`. It holds the resolved arguments, the
  workflow's progress, and the pending instruction, so work continues after a
  restart.
- **Instruction**: the next request to the agent. It contains guidance,
  concise context, whether the user must answer first, and the replies that
  may continue the session, each with its argument schema.

## Use a workflow

`hiraku init` installs the bundled workflows and registers the Hiraku MCP
server for Claude Code (`.mcp.json`) and Codex (`.codex/config.toml`). The
server runs `hiraku mcp`, so the `hiraku` command must be on the agent's
`PATH`. Each workflow is available in two ways under its ID:

- as an **MCP prompt** that you invoke yourself, which agents such as Claude
  Code show as a slash command, e.g. `/mcp__hiraku__collaborative`. Its
  arguments are typed in the command; the prompt starts a session and hands its
  first instruction to the agent.
- as an **MCP tool** that the agent calls when you ask for the workflow, e.g.:

  > What Hiraku workflows are available here? Start collaborative research on
  > the active task, at most two experiments.

  The agent calls `workflow_list` to explain the workflows, then the
  `collaborative` tool. Codex has no MCP prompts, so this is how it starts
  workflows.

Every response names the replies that continue the session. The agent answers
with the `workflow_reply` tool, for example
`workflow_reply(reply="revise", arguments={"feedback": "Try dropout"}, ...)`:

```json
{
  "status": "accepted",
  "session_id": "collaborative-3f9a1c2b4d5e",
  "instruction_id": 2,
  "instruction": {
    "text": "## Approve\n\nPresent the experiment to the user ...",
    "context": {"record_id": "exp-reg", "hypothesis": "Regularization helps"},
    "requires_user_response": true,
    "replies": [
      {"reply": "approve", "description": "Record the user's approval ...",
       "arguments": {"properties": {}}},
      {"reply": "revise", "description": "Propose a different experiment ...",
       "arguments": {"properties": {"feedback": {"type": "string"}},
                     "required": ["feedback"]}},
      {"reply": "finish", "arguments": {"properties": {}}}
    ]
  }
}
```

When `requires_user_response` is true, the agent asks you before replying. This
is a conversational contract; it does not authenticate approval.

A reply passes the `instruction_id` of the instruction it answers, so a late or
repeated call cannot be applied to a later step. `session_id` may be left out
while only one session is active. Hiraku checks the reply's arguments against
its schema and keeps each accepted call in the session's history.

A reply has one of these outcomes:

| Status | Meaning |
| --- | --- |
| `accepted` | The reply was applied; the response holds the next instruction or `completed: true` and a result. |
| `rejected` | The input was invalid; the same instruction stays pending. |
| `not_ready` | The input was valid, but the evidence it refers to has not arrived yet; check again later. |
| `stale` | The reply answers an instruction that is no longer pending; the call was not applied, and the response holds the current instruction. |

Repeating an identical reply is safe: it returns the same response without
applying the reply again.

Completion means the session ended, not necessarily that every objective was
met: results distinguish, for example, completed, partial, and stopped.

### Resume, inspect, and cancel

Sessions are stored on disk, so a restarted MCP server or a new conversation
continues where the session stopped. Ask the agent to resume the session; it
uses `session_list` and `session_show` to get the pending instruction. From a
terminal:

```bash
hiraku session list
hiraku session show <session-id>
hiraku session cancel <session-id>
```

Cancelling stops the session from accepting replies. It never reverts code,
deletes records, or stops processes that are still running.

Hiraku resumes the last saved instruction and never applies an accepted reply
twice. Work the agent did before replying, such as creating records or
launching runs, is not rolled back; workflows tell the agent to check for
existing outputs before repeating such work.

### Tools and prompts are fixed

The MCP server offers one tool and one prompt per workflow, the generic
`workflow_reply`, and the tools below, all fixed when it starts: coding agents
do not reliably pick up tools that change during a session. A workflow added or
edited later appears after you reconnect the Hiraku MCP server or restart the
agent.

### MCP server tools

Besides the workflow tools, the server offers `workflow_list`, `session_list`,
`session_show`, `session_cancel`, `project_status`, `record_list`,
`record_show`, `record_create`, `record_update`, and `runs`, and resources for
workflow documents (`hiraku://workflows/<id>`), records
(`hiraku://records/<id>`), sessions (`hiraku://sessions/<id>`), and contexts
(`hiraku://contexts/<id>`).

Experiments are launched with the `hiraku launch` CLI. MCP clients limit how
long a tool call may run and how much output it may return, so long launches
are not run as MCP tools. The server's environment and working directory come
from the agent's MCP configuration, not from the agent's shell.

## Write a Markdown-only workflow

The simplest workflow is a directory with only `workflow.md`. Its front matter
may declare arguments, which fill `{{name}}` placeholders in the body:

```markdown
---
id: literature-scan
description: Scan recent papers on the task and summarize them in a context.
arguments:
  topic: {required: true, description: What to search for}
  years: {type: integer, default: 2, description: How far back to search}
---
# Literature scan

Search for papers on {{topic}} from the last {{years}} years ...
```

Argument types are `string` (the default), `integer`, `number`, and `boolean`.
A placeholder without a declared argument is an error when the workflow loads.

`/mcp__hiraku__literature-scan topic=diffusion years=3` and the
`literature-scan` tool both start a session whose only instruction is the
rendered body. Prompt arguments arrive as text and are converted to the
declared types. The single reply `complete` ends the session with the agent's
summary; sessions can be resumed, inspected, and cancelled like any other.

Hiraku checks nothing about the work in a Markdown-only workflow: there are no
budgets, approvals, or evidence checks, and `complete` only means the agent
reports that it is done. Add a `workflow.py` when you need such checks; the
`workflow.md` stays the same record.

## Write a workflow

Create `hiraku/workflows/<workflow-id>/workflow.py` exporting `WORKFLOW`, and a
`workflow.md` with the prompts:

```python
from typing import Annotated

from pydantic import Field

from hiraku.errors import SubmissionRejected
from hiraku.workflow import Session, Workflow, reply


class ReviewSession(Session):
    topic: str
    questions: list[str] = []


class Review(Workflow):
    """Search the literature and choose a research question with the user."""

    min_questions: int = 2  # setting; override it in the front matter

    def start(
        self, topic: Annotated[str, Field(description="Research topic.")]
    ):
        """Start a literature review."""
        session = ReviewSession(topic=topic)
        return self.instruction(
            session,
            self.section("search"),
            context={"topic": topic},
            replies=[self.propose],
        )

    @reply
    def propose(self, session: ReviewSession, questions: list[str]):
        """Submit candidate research questions."""
        if len(questions) < self.min_questions:
            raise SubmissionRejected(f"Propose at least {self.min_questions} questions")
        session.questions = questions
        return self.instruction(
            session,
            self.section("choose"),
            context={"questions": questions},
            requires_user_response=True,
            replies=[self.choose],
        )

    @reply
    def choose(self, session: ReviewSession, question: str):
        """Record the question the user chose."""
        if question not in session.questions:
            raise SubmissionRejected(f"Choose one of {session.questions}")
        return self.completion(session, {"question": question})


WORKFLOW = Review
```

```markdown
---
id: review
min_questions: 3
---
# Literature review

Introductory guidance shown when the session starts.

## Search

Search the literature on the topic and propose research questions.

## Choose

Present the questions and ask the user which one to pursue.
```

The rules are deliberately small:

- A `Workflow` instance is the loaded `workflow.md` record. Its front matter
  needs the workflow `id`, matching the directory name. Workflow IDs use
  letters, digits, and `-`, so the MCP tool named after a workflow never
  collides with Hiraku's own tools such as `workflow_reply`. Annotated class
  attributes, like `min_questions` above, are settings validated from the front
  matter; other front-matter fields are kept as metadata. The class body
  follows Pydantic rules: constants need `ClassVar[...]`, and `__init__` is not
  overridden.
- `start` creates a session and returns its first instruction. Its signature
  and docstring define how the workflow is started: they become the MCP tool
  and prompt named after the workflow. Use `Annotated[..., Field()]` for
  parameter descriptions and constraints.
- `@reply` marks a method that continues a session. Its first parameter is
  the session, annotated with the workflow's `Session` subclass; the other
  parameters and the docstring define the reply's arguments and description.
  Hiraku loads the session, checks that the reply answers the pending
  instruction and that its arguments match, calls the method, and saves the
  result atomically. A reply cannot be named like a `Workflow` method such as
  `save` or `section`.
- A `Session` subclass declares the workflow's own data. The base fields, such
  as `id`, `status`, `instruction_id`, and `history`, belong to the runtime and
  cannot be redefined.
- `start` and replies return `self.instruction(...)` or
  `self.completion(...)`. An instruction lists the replies that may answer it,
  e.g. `replies=[self.propose]`.
- Raise `SubmissionRejected` for invalid input and `NotReady` when valid input
  refers to evidence that has not arrived yet. Nothing is saved in either case.
  A valid alternative, such as revising, skipping, or stopping, is a normal
  reply that returns the next instruction.
- `self.section("name")` returns a section of this workflow's `workflow.md`,
  and `self.text` the whole body. Section names are lowercase heading text
  with punctuation removed and spaces replaced by hyphens. The text before the
  first `##` heading is the orientation shown at start.
- Each call gets a fresh copy of the loaded workflow, bound to the project
  configuration as currently saved; changing `self` during a
  call is not persisted. To run a workflow from Python, for example in a
  test, load it with `hiraku.workflow.load_workflow()`, bind it with
  `workflow.bind(config)`, and call `run_start({...})` and
  `run_reply(session_id, instruction_id, "propose", {...})`.
- Use `self.config` and the Hiraku Python API to read and verify records and
  runs. Prefer replies that name records or runs, which the workflow loads and
  checks, over replies that carry results as free text.
- Workflow modules can import helpers from their own directory with relative
  imports.

The MCP server loads workflow code and prompts when it starts. A workflow that
fails to load is logged to the server's stderr and left out;
`hiraku workflow list` in a terminal shows the error. After editing a workflow,
reconnect the MCP server. Existing sessions
continue with the new code if their stored data still validates against the
session model and their pending replies still exist; otherwise their replies
are rejected, and they remain inspectable.
Keep workflows compatible with existing sessions, or start new ones.
Workflow code is trusted project code: loading it executes it.

Test a workflow without an agent with `hiraku workflow start` and
`hiraku workflow reply`, which print the JSON responses:

```bash
echo '{"topic": "protein folding"}' | hiraku workflow start review --arguments-file -
echo '{"questions": ["Q1", "Q2"]}' | hiraku workflow reply <session-id> propose --instruction-id 1 --arguments-file -
```

## Bundled workflows

The [autoresearch example](../examples/autoresearch/README.md) also installs
`autoresearch`, a Markdown-only workflow with the upstream
"experiment until stopped" procedure.

- **collaborative** shows a gated flow in a few lines of code: the agent
  proposes one experiment record at a time, you approve it before any work
  (or ask for a revision), the agent implements and launches it, and you
  discuss each result before deciding to continue. Hiraku checks that the
  proposal is an experiment under the task and that the submitted run is
  attributed to it, and stops after the `rounds` budget.
- **autodataset** sends one complete instruction to find datasets for a task
  and document each as a `type: dataset` record with `sources`. A shortfall is
  a valid reply: you decide whether to keep searching or accept a partial
  result. Hiraku checks the records' structure and sources, not licensing or
  suitability.
