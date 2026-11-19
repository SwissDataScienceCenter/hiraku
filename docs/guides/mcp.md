# Connect a coding agent

Run `hiraku init` in your project to configure the Hiraku MCP server for Claude Code, Codex, and Pi. After cloning an initialized project, run `hiraku mcp install` to restore missing client entries, including the local Codex configuration.

The client launches `hiraku mcp serve` from the project directory. Make sure it can find `hiraku` on its PATH. If your client requires you to trust the project or approve its MCP server, do that before opening the tool list.

To configure another client, use `hiraku` as the command and `mcp serve` as its arguments. Set its working directory inside your Hiraku project.

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
## Work with records and runs

Use `record_list` and `record_show` to inspect research history, then `job_submit` to launch a command for a record. Follow the returned job ID with `job_status`. Use `runs` to query metrics for that record.

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

## Run a workflow

Discover workflows with `workflow_list`. Use `workflow_start` to start a session, `workflow_status` to inspect it, and `workflow_resume` with the checkpoint ID and response to continue after an interrupt. When an interrupt requests approval, `workflow_approval` can collect an approve, revise, or stop decision. Leave the session paused if the client cancels the request.

See [Workflows](workflows.md) for graph authoring and checkpoint behavior.
