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
For agent-launched commands, prefer the `job_submit` MCP tool. Read command
output with `job_logs`; call `job_status` or `job_list` when execution state is
needed. Use `job_cancel` to stop a running job. The bundled Hiraku skill gives
the same guidance and describes CLI fallback when MCP tools are unavailable or
the user asks for CLI commands.

## Tools

| Tools | Behavior |
| --- | --- |
| `project_info` | Read the current project settings. |
| `leaderboard_list` | List leaderboard names without loading definitions, like `hiraku leaderboard list --json`. |
| `record_list` | List every matching record's metadata, without Markdown content. Use `record_show` for local fields and Markdown. |
| `record_show` | Read one record's local fields and Markdown. |
| `record_create`, `record_update` | Persist a record and return its ID plus the supplied or patched field names, without echoing field values or Markdown. |
| `record_delete` | Delete a record using the same relationship checks as the Python API. |
| `job_submit` | Submit through PSI/J, record full history, and return a compact receipt with the original job `id`, record ID, executor, and initial state. |
| `job_list` | List every matching job as a summary (`id`, `record_id`, `executor`, and `state`), refreshing and saving nonfinal jobs; optionally filter by `record_id`. Use `job_status` for exit code and message, and `job_logs` for output. |
| `job_status` | Attach through PSI/J, save, and return the job ID plus state, exit code, and message. |
| `job_logs` | Read available stdout and stderr using the full job ID or a unique nonempty prefix, without refreshing status. |
| `job_cancel` | Cancel a job using the full job ID or a unique nonempty prefix and return its resolved ID and resulting state. |
| `runs` | List runs from a named `leaderboard`, optionally filtered by `record_id`. Results are capped at 100 by default; set `limit` to choose another cap. Set `include_unattributed` to include runs without a record ID; metric sorting follows leaderboard declarations. |


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
