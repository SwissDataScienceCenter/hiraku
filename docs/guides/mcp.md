# MCP server

Hiraku includes an MCP server that works with any initialized Hiraku project.
`hiraku init` and `hiraku example create` install project-scoped MCP
configuration for Claude Code, Codex, and Pi. After cloning a shared project,
run `hiraku mcp install` to add the local Codex configuration and restore any
missing client entries.
The server process discovers the nearest `hiraku.yml` from its working directory,
so each client starts it in the project it is working on:

```bash
uv run hiraku mcp serve
```

There is no project-path flag; the process uses its working directory.

The Codex config is ignored by Git so local MCP tool preferences remain
private. Run `hiraku mcp install` after cloning to create it on your machine.

For reference, initialization creates or merges these host configurations:

Claude Code's `.mcp.json`:

```json
{
  "mcpServers": {
    "hiraku": {
      "command": "hiraku",
      "args": ["mcp", "serve"]
    }
  }
}
```

Codex's `.codex/config.toml`:

```toml
[mcp_servers.hiraku]
command = "hiraku"
args = ["mcp", "serve"]
```

Pi's `.pi/mcp.json`:

```json
{
  "mcpServers": {
    "hiraku": {
      "command": "hiraku",
      "args": ["mcp", "serve"]
    }
  }
}
```

An existing `hiraku` server entry is left as it is. If an existing file cannot
be extended, installation fails without changing that file; add the entry
manually or repair the configuration and rerun `hiraku mcp install`.

The host must be able to find the installed `hiraku` command. Claude Code asks
for approval before using a project MCP server. Codex loads project config only
for trusted projects. The MCP tools are exposed directly by the host, so no
extra `AGENTS.md` or Claude skill instructions are needed to register them.
The bundled Hiraku skill tells agents to use the tools when they are available
and fall back to the CLI when the host has not connected the server.

## Tools

| Tools | Behavior |
| --- | --- |
| `project_info` | Read the current project settings. |
| `leaderboard_list` | List leaderboard names without loading definitions, like `hiraku leaderboard list --json`. |
| `record_list`, `record_show` | Read local record fields and Markdown. |
| `record_create`, `record_update`, `record_delete` | Persist research records using the same validation and relationship checks as the Python API. |
| `job_submit` | Submit through PSI/J (default: detached) and return after acceptance. |
| `job_list`, `job_show` | Observe jobs automatically; pass `cached` for saved observations. |
| `job_wait`, `job_logs`, `job_cancel` | Read saved output or request cancellation. |
| `runs` | List attributed runs from a named `leaderboard`, optionally filtered by `record_id`. Set `include_unattributed` to include all source runs; metric sorting follows leaderboard declarations. |

Create tools accept a Markdown body and a `fields` object for additional metadata.
For `record_create`, `fields` must include a nonblank `type`.
Record creation activates the record by default; pass `activate: false` to leave
the active record unchanged. Update tools merge `fields` and optionally replace
the Markdown body. Explicit `null` values are retained in metadata. IDs and persistence
paths and the organizational parent cannot be changed through the update tool.
Expected failures are returned as MCP tool errors.

`job_submit` requires explicit command arguments. Commands are not stored in
records. It defaults to detached execution and returns after startup, without waiting
for completion. `executor="local"` is an alias for `detached`.
The detached adapter owns its worker and state;
Hiraku persists observations
in `job.json`. Use `job_wait` and `job_logs` separately. MCP does not expose `job_run`.
A connected client can run commands with the user's local permissions.

## Resources and evidence

Read `hiraku://catalog` for a JSON list of current OKF record IDs. The catalog and
resources are loaded on each request, including after changes made outside MCP.

| URI | Content |
| --- | --- |
| `hiraku://records/{record_id}` | Record fields and Markdown as JSON. |
| `hiraku://workflows/{workflow_id}` | Prompt document of a workflow. |
| `hiraku://workflows/{workflow_id}/graph` | LangGraph topology as Mermaid text. |

Percent-encode record IDs when constructing resource URIs, including slashes:
the ID `literature/review` becomes `hiraku://records/literature%2Freview`.

## Workflows

Project workflows are listed with `workflow_list`, started with
`workflow_start(workflow_id, arguments)`, inspected with
`workflow_status(workflow_session_id)`, and resumed with
`workflow_resume(workflow_session_id, response, checkpoint_id)`. Every workflow is a LangGraph graph defined
by `build_graph(checkpointer, *, project)` in `workflow.py`; there is no
Hiraku-specific event protocol or per-workflow MCP tool. The workflow session ID is the
LangGraph thread ID, and resumable graph state is checkpointed in
`.hiraku/langgraph-checkpoints.sqlite` by default. See the [Workflows guide](workflows.md).

Any workflow interrupt can request approval by including an `approval` object
in its interrupt payload. `workflow_approval` takes the workflow session ID, interrupt ID,
proposal, and proposed response, and uses MCP elicitation to collect the
standard approve, revise, or stop decision. Pass its result to
`workflow_resume`. If elicitation is cancelled, leave the graph paused.
Approval is cooperative: Python, CLI, and MCP callers can submit decisions
directly and must be trusted. MCP elicitation does not restrict other tools or
local shell access.

## Prompt template

The optional `propose_implementation` prompt takes a `record_id` and a
comma-separated `related_record_ids` string. It includes the selected and
related records and asks the agent to cite evidence, distinguish assumptions, discover
other records and describe validation before making changes. It returns
instructions only; it does not run an agent or modify the project.

## Python

`hiraku.mcp.create_server(project=None)` returns the SDK server. Pass an existing
`Project` to select a project explicitly; otherwise it discovers the
current project. Call `server.run(transport="stdio")` to serve it.
