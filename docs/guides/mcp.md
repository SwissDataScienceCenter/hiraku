# Connect a coding agent

Run `hiraku init` in your project to configure the Hiraku MCP server for Claude Code, Codex, and Pi. After cloning an initialized project, run `hiraku mcp install` to restore missing client entries, including the local Codex configuration.

The client launches `hiraku mcp serve` from the project directory. Make sure it can find `hiraku` on its PATH. If your client requires you to trust the project or approve its MCP server, do that before opening the tool list.

To configure another client, use `hiraku` as the command and `mcp serve` as its arguments. Set its working directory inside your Hiraku project.

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
