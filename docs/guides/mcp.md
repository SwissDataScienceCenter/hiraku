# MCP server

Hiraku includes an MCP server that works with any initialized Hiraku project.
`hiraku init` adds project-scoped MCP configuration for Claude Code and Codex.
The server process discovers the nearest `hiraku.yml` from its working directory,
so each client starts it in the project it is working on:

```bash
uv run hiraku mcp
```

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

An existing `hiraku` server entry is left as it is. If an existing file cannot
be extended, for example because `mcp_servers` is an inline TOML table,
initialization fails without changing it; add the entry manually.

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
A connected client can run commands with the user's local permissions.

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

Project workflows are LangGraph graphs. Use `workflow_list` to discover them,
`workflow_start` to start one, `workflow_status` to inspect it, and
`workflow_resume` to continue an interrupted session. See the
[Workflows guide](workflows.md).

## Prompt template

The optional `propose_implementation` prompt takes a `record_id` and a
comma-separated `related_record_ids` string. It includes the selected and
related records and asks the agent to cite evidence, distinguish assumptions, inspect
attached sources and describe validation before making changes. It returns
instructions only; it does not run an agent or modify the project.

## Python

`hiraku.mcp.create_server(config=None)` returns the SDK server. Pass an existing
`HirakuConfig` to select a project explicitly; otherwise it discovers the
current project. Call `server.run(transport="stdio")` to serve it.
