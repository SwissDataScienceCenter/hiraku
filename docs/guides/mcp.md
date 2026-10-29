# MCP server

Hiraku includes an MCP server that works with any initialized Hiraku project.
`hiraku init` adds project-scoped MCP configuration for Claude Code and Codex.
The server process discovers the nearest `hiraku.yml` from its working directory,
so each client starts it in the project it is working on:

```bash
uv run hiraku mcp
```

Workflow start, event, and session-show tools return attached records as native
MCP resource links alongside their JSON response. Read those resources before
carrying out the instruction. Resources provide live contents; stored
instructions contain only references. All records use `application/json` at
`hiraku://records/...` URIs, regardless of their `type`.
Source files and parent records are not attached automatically. A deleted
attachment is returned as a link with an unavailability diagnostic; accepted
session progress is retained. Prompts remain text-based and list the same IDs.

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
| `launch_entrypoint` | Execute a configured record entrypoint through the dispatcher, preserving attribution. |
| `runs` | List tracked runs attributed to a record, like `hiraku runs`; `all_records` lists every attributed run in its tracking source. |

Create tools accept a Markdown body and a `fields` object for additional metadata.
Record creation activates the record by default; pass `activate: false` to leave
the active record unchanged. Update tools merge `fields` and optionally replace
the Markdown body. Set a field to `null` to clear its value. IDs and persistence
paths cannot be changed; use `record_reparent` to change the organizational parent.
Expected failures are returned as MCP tool errors.

`launch_entrypoint` requires explicit `record_id` and `entrypoint_name`, with
optional argument overrides, commit and dispatcher selection. Pass `dry_run: true`
to preview the effective command without execution. Executed launches return
`returncode`, `stdout` and `stderr`; a nonzero exit is reported in the result.
Output is captured so dispatcher output cannot corrupt the stdio protocol.
Launches wait for the dispatcher to finish, just like the CLI; dispatchers may
submit asynchronous jobs themselves. A client connected to this server can
modify project metadata and execute configured entrypoints with the user's local
permissions.

## Resources and evidence

Read `hiraku://catalog` to discover the current resource URIs. The catalog and
resources are loaded on each request, including after changes made outside MCP.

| URI | Content |
| --- | --- |
| `hiraku://records/{record_id}` | Record fields and Markdown as JSON. |
| `hiraku://workflows/{workflow_id}` | Prompt document of a workflow. |
| `hiraku://sessions/{session_id}` | Stored state of a workflow session as JSON. |
| `hiraku://sources/{record_id}/{source_path}` | File attached to a record as an MCP blob with its inferred MIME type. |

Place PDFs, literature reviews and other source files inside a record directory,
for example `hiraku/records/literature/study.pdf`, alongside `literature.md`.
The catalog returns percent-encoded source paths, including encoded
slashes for nested directories. Use those URIs directly. File access rejects
paths and symlinks escaping the selected record directory. PDF bytes are served
with `application/pdf`; interpretation depends on the MCP client's capabilities.

## Workflows

Each project workflow is a tool and a prompt named after it, and events go
through `workflow_send_event`. `workflow_list` and the `session_*` tools list
workflows and inspect, resume, or cancel sessions. See the
[Workflows guide](workflows.md).

The conversational server wraps core event execution and attaches the saved next
instruction after accepted or stale submissions. Core execution returns only a
typed event result. Workflow instances reload their bound project configuration
on access.

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
