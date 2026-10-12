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
  concise context, whether the user must answer first, and the reply tools that
  may continue the session.

## Use a workflow

`hiraku init` installs the bundled workflows and registers the Hiraku MCP
server for Claude Code (`.mcp.json`) and Codex (`.codex/config.toml`). The
server runs `hiraku mcp`, so the `hiraku` command must be on the agent's
`PATH`. Start your agent in the project and ask it to start a workflow:

> What Hiraku workflows are available here? Start guided research on the
> active task with three hypotheses per round.

The agent calls `workflow_list` to explain the workflows and their arguments,
then `guided-research_start`. Every response names the reply tools that
continue the session, for example:

```json
{
  "status": "accepted",
  "session_id": "guided-research-3f9a1c2b4d5e",
  "instruction_id": 2,
  "instruction": {
    "text": "## Choose\n\nPresent the candidates briefly ...",
    "context": {"candidates": [{"id": "exp-reg"}, {"id": "exp-lr"}]},
    "requires_user_response": true,
    "replies": [
      {"tool": "guided-research_select",
       "arguments": {"session_id": "guided-research-3f9a1c2b4d5e",
                     "instruction_id": 2}},
      {"tool": "guided-research_revise", "arguments": {"...": "..."}},
      {"tool": "guided-research_finish", "arguments": {"...": "..."}}
    ]
  }
}
```

When `requires_user_response` is true, the agent asks you before replying. This
is a conversational contract; it does not authenticate approval.

A reply has one of these outcomes:

| Status | Meaning |
| --- | --- |
| `accepted` | The reply was applied; the response holds the next instruction or `completed: true` and a result. |
| `rejected` | The input was invalid; the same instruction stays pending. |
| `not_ready` | The input was valid, but the evidence it refers to has not arrived yet; check again later. |
| `duplicate` | The same reply was already applied; the current response is returned. |
| `conflict`, `stale` | An earlier instruction was already answered differently; the call was not applied. |
| `blocked` | The loaded workflow code no longer matches the session; inspect it and start a new one. |

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

### Choose which workflows are exposed

The MCP server exposes tools only for the workflows listed in `hiraku.yml`:

```yaml
mcp:
  workflows: [autoresearch-python, autodataset]
```

Leave out `mcp.workflows` to expose all workflows, or use `[]` to expose none.
`hiraku workflow activate ID` and `hiraku workflow deactivate ID` edit the list;
the agent can do the same with `workflow_activate` and `workflow_deactivate`.
Hiraku then notifies the agent that its tools changed. Depending on the agent,
new tools appear in the next message or only after reconnecting the MCP server
or restarting the agent. `hiraku mcp --workflow ID` overrides the list
for one connection. Inactive workflows still appear in `workflow_list`, and
their sessions can be inspected.

### MCP server tools

Besides the workflow tools, the server offers `project_status`, `record_list`,
`record_show`, `record_create`, `record_update`, and `runs`, and resources for
workflow prompts (`hiraku://workflows/<id>`), records
(`hiraku://records/<id>`), sessions (`hiraku://sessions/<id>`), and contexts.
Each active workflow is also offered as an MCP prompt, which some agents show
as a slash command, and workflows can declare their own resources and prompts.

Experiments are launched with the `hiraku launch` CLI. MCP clients limit how
long a tool call may run and how much output it may return, so long launches
are not run as MCP tools. The server's environment and working directory come
from the agent's MCP configuration, not from the agent's shell.

## Write a Markdown-only workflow

The simplest workflow is a directory with only `workflow.md`:

```markdown
---
id: literature-scan
description: Scan recent papers on the task and summarize them in a context.
---
# Literature scan

Search for papers from the last two years on the active task ...
```

Hiraku sends the whole body as one instruction. `literature-scan_start` takes an
optional free-text `request` with the user's goals or limits, and the single
reply `literature-scan_complete` ends the session with the agent's summary.
Sessions can be resumed, inspected, and cancelled like any other.

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
from hiraku.workflow import Session, Workflow, tool


class ReviewSession(Session):
    topic: str
    questions: list[str] = []


class Review(Workflow):
    """Search the literature and choose a research question with the user."""

    min_questions: int = 2  # setting; override it in the front matter

    @tool
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

    @tool
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

    @tool
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
  letters, digits, and `-`, but not `_`, which separates the workflow ID from
  the tool name in MCP names. Annotated class
  attributes, like `min_questions` above, are settings validated from the front
  matter; other front-matter fields are kept as metadata. The class body
  follows Pydantic rules: constants need `ClassVar[...]`, and `__init__` is not
  overridden.
- `@tool` registers a method as an MCP tool named `<workflow-id>_<method>`; use
  `@tool("name")` to choose the tool name separately, for example when the
  method name is taken by a record attribute such as `save`, `load`, or
  `delete`. The signature and docstring define the tool schema; use
  `Annotated[..., Field()]` for parameter descriptions and constraints.
- `start` creates sessions. A method with one parameter annotated as a `Session`
  subclass is a **reply**: Hiraku loads the session, calls the method, and saves
  the result atomically. Any other `@tool` method is an ordinary tool that does
  not change sessions.
- Replies return `self.instruction(...)` or `self.completion(...)`. An
  instruction lists the replies that may answer it.
- Raise `SubmissionRejected` for invalid input and `NotReady` when valid input
  refers to evidence that has not arrived yet. Nothing is saved in either case.
  A valid alternative, such as revising, skipping, or stopping, is a normal
  reply that returns the next instruction.
- `self.section("name")` returns a section of this workflow's `workflow.md`,
  and `self.text` the whole body. Section names are lowercase heading text
  with punctuation removed and spaces replaced by hyphens. The text before the first `##` heading is the orientation shown at
  start.
- Each call gets a fresh copy of the loaded workflow, bound to the project
  configuration as currently saved; changing `self` during a
  call is not persisted. `hiraku.workflow.load_workflow()` loads a workflow
  from Python, for example to test it.
- Use `self.config` and the Hiraku Python API to read and verify records and
  runs. Prefer replies that name records or runs, which the workflow loads and
  checks, over replies that carry results as free text.
- `@resource` exposes a method's returned text as the MCP resource
  `hiraku://workflows/<workflow-id>/<name>`, and `@prompt` exposes one as the
  MCP prompt `<workflow-id>_<name>` whose `str` parameters are the prompt
  arguments. As with `@tool`, the name is the method name unless given, as in
  `@prompt("name")`. Both are listed only while the workflow is active and
  never change sessions.
- Workflow modules can import helpers from their own directory with relative
  imports. `hiraku.research` provides the task, run, and evidence helpers the
  bundled research workflows share.

The MCP server loads workflow code and prompts when it starts. A workflow that
fails to load is logged to the server's stderr and left out; activating it with
`workflow_activate` loads it again and returns the error. After editing a
workflow that already loaded, reconnect the MCP server. Existing sessions continue with the new
code if their stored data still validates against the session model and their
pending replies still exist; otherwise they are blocked and remain inspectable.
Keep workflows compatible with existing sessions, or start new ones.
Workflow code is trusted project code: loading it executes it.

Test a workflow without an agent with `hiraku workflow call`:

```bash
echo '{"topic": "protein folding"}' | hiraku workflow call review_start --arguments-file -
```

## Bundled workflows

The [autoresearch example](../examples/autoresearch/README.md) also installs
`autoresearch`, a Markdown-only workflow with the upstream
"experiment until stopped" procedure.

- **autoresearch-python** follows the autoresearch loop autonomously, without
  asking you: establish a baseline, then for each of `iterations` experiments
  choose one idea, commit it, create a bound experiment record, launch it, and
  submit the run. Hiraku checks the run and decides `keep`, `discard`, or
  `crash`; the agent records the decision and, for discard and crash, resets the
  branch to the retained commit, as in the upstream loop. Its evidence policy
  (resource `hiraku://workflows/autoresearch-python/policy`) requires a run attributed
  to an experiment record under the task, its bound commit, and the
  entrypoint; an explicit success status (setting `success_status`, default
  `finished`); a clean working tree when recorded; and a finite metric. The
  `autoresearch-python_resume` prompt resumes the latest session.
- **guided-research** is the collaborative counterpart: the agent proposes
  hypotheses, you choose one, approve its exact launch, and discuss each
  result before deciding to continue. A run counts only if it matches the
  approved record, commit, entrypoint, and arguments; a changed configuration
  needs a new approval.
- **autodataset** sends one complete instruction to find datasets for a task
  and document each as a `type: dataset` record with `sources`. A shortfall is
  a valid reply: you decide whether to keep searching or accept a partial
  result. Hiraku checks the records' structure and sources, not licensing or
  suitability.
