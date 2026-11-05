# MCP server

Hiraku includes an MCP server that works with any initialized Hiraku project.
`hiraku init` and `hiraku example create` add project-scoped MCP configuration
for Claude Code, Codex, and Pi.
The server process discovers the nearest `hiraku/hiraku.yml` from its working directory,
so each client starts it in the project it is working on:

```bash
uv run hiraku mcp
```

For local development, launch the MCP Inspector with:

```bash
uv run hiraku mcp --dev
```

This requires the MCP CLI (`mcp[cli]`) and Node.js with `npx` on `PATH`. The
Inspector can start without an initialized project; project operations need a
project to be discoverable when they are called.

There is no project-path flag; the process uses its working directory. Each
initialized project gets its own host configuration, and agents connected from
that project receive the same Hiraku tools and resources.

For reference, initialization creates or merges these host configurations:

Claude Code's `.mcp.json`:

```json
{
  "mcpServers": {
    "hiraku": {
      "command": "hiraku",
      "args": ["mcp"]
    }
  }
}
```

Codex's `.codex/config.toml`:

```toml
[mcp_servers.hiraku]
command = "hiraku"
args = ["mcp"]
```

Pi's `.pi/mcp.json`:

```json
{
  "mcpServers": {
    "hiraku": {
      "command": "hiraku",
      "args": ["mcp"]
    }
  }
}
```

An existing `hiraku` server entry is left as it is. If an existing file cannot
be extended, initialization fails without changing it; add the entry manually.

The host must be able to find the installed `hiraku` command. Claude Code asks
for approval before using a project MCP server. Codex loads project config only
for trusted projects. The MCP tools are exposed directly by the host, so no
extra `AGENTS.md` or Claude skill instructions are needed to register them.
The bundled Hiraku skill tells agents to use the tools when they are available
and fall back to the CLI when the host has not connected the server.

## Tools

| Tools | Behavior |
| --- | --- |
| `record_list`, `record_show` | Read records; show includes inherited values and their sources. |
| `record_create`, `record_update`, `record_delete` | Persist research records using the same validation and relationship checks as the Python API. |
| `record_reparent` | Move a record subtree under a parent, or make it a root. |
| `execute` | Run a command in the foreground and return its exit code, stdout, and stderr. |
| `runs` | List tracked runs attributed to a record, like `hiraku runs`; `all_records` lists every attributed run in its tracking source. |

Create tools accept a Markdown body and a `fields` object for additional metadata.
Record creation activates the record by default; pass `activate: false` to leave
the active record unchanged. Update tools merge `fields` and optionally replace
the Markdown body. Set a field to `null` to clear its value. IDs and persistence
paths cannot be changed; use `record_reparent` to change the organizational parent.
Expected failures are returned as MCP tool errors.

`execute` requires explicit command arguments. Commands are not stored in
records. It waits for completion and returns the exit code, stdout, and stderr.
A connected client can run commands with the user's local permissions. If the
client cancels the MCP call, Hiraku terminates the running command.

## Resources and evidence

Read `hiraku://catalog` to discover the current resource URIs. The catalog and
resources are loaded on each request, including after changes made outside MCP.

| URI | Content |
| --- | --- |
| `hiraku://records/{record_id}` | Record fields and Markdown as JSON. |
| `hiraku://workflows/{workflow_id}` | Prompt document of a workflow. |
| `hiraku://sources/{record_id}/{source_path}` | File attached to a record as an MCP blob with its inferred MIME type. |
| `hiraku://workflows/{workflow_id}/graph` | LangGraph topology as Mermaid text. |

Place PDFs, literature reviews and other source files inside a record directory,
for example `hiraku/records/literature/study.pdf`, alongside `literature.md`.
The catalog returns percent-encoded source paths, including encoded
slashes for nested directories. Use those URIs directly. File access rejects
paths and symlinks escaping the selected record directory. PDF bytes are served
with `application/pdf`; interpretation depends on the MCP client's capabilities.

## Workflows

Project workflows are listed with `workflow_list`, started with
`workflow_start(workflow_id, arguments)`, inspected with
`workflow_status(workflow_session_id)`, and resumed with
`workflow_resume(workflow_session_id, response, checkpoint_id)`. Every workflow is a LangGraph graph defined
by `build_graph(checkpointer, *, project)` in `workflow.py`; there is no
Hiraku-specific event protocol or per-workflow MCP tool. The workflow session ID is the
LangGraph thread ID, and resumable graph state is checkpointed in
`.hiraku/langgraph-checkpoints.sqlite` by default. See the [Workflows guide](workflows.md).

Workflow interrupts identify the next actor (`agent` or `human`) and interaction
type (`task`, `review`, or `approval`). Agent-owned tasks are requests for the
coding agent to perform work and submit a response; they never trigger MCP
elicitation. Human-owned reviews and approvals use MCP form elicitation, and a
decision supplied only in ordinary tool arguments cannot authorize the graph.
Legacy `interaction.mode` and approval-only interrupts remain supported.

`workflow_start`, `workflow_status`, and `workflow_resume` return normalized
`next_action` for a single pending interrupt and `next_actions` for all
interrupts. If multiple interrupts are pending, `next_action` is null and the
MCP resume resolver refuses an ambiguous single response. A workflow may
checkpoint a proposal first, then return a distinct human approval interrupt
bound to its stored revision. After approval, it returns a separate agent task
with the approved action and a result schema. A `waiting` status can indicate
the next phase rather than a stalled workflow. `workflow_status` only inspects
the pending interrupt; it does not elicit or advance the workflow. If
elicitation is cancelled, leave the graph paused.
If the client lacks form elicitation support, `workflow_resume` returns a
capability error with reconnect guidance and leaves the graph paused.
The actor is declared by workflow code and enforced by the MCP resume resolver.
Local Python and CLI interfaces should be exposed only to an authorized user.

The server also registers one MCP prompt per workflow, named with the workflow
ID. Prompt arguments are derived from the workflow's `input_schema`; they are
presented as strings with field descriptions, and object, array, numeric, and
boolean values should be supplied as JSON text. The prompt converts those
values and returns a suggested `workflow_start` call. Retrieving the prompt
does not start a workflow session; call `workflow_start` to run it.

## Prompt template

The optional `propose_implementation` prompt takes a `record_id` and a
comma-separated `related_record_ids` string. It includes the selected and
related records and asks the agent to cite evidence, distinguish assumptions, inspect
attached sources and describe validation before making changes. It returns
instructions only; it does not run an agent or modify the project.

## Python

`hiraku.mcp.create_server(project=None)` returns the SDK server. Pass an existing
`Project` to select a project explicitly; otherwise it discovers the
current project. Call `server.run(transport="stdio")` to serve it.
