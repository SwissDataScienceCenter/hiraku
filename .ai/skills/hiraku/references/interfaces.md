# Hiraku interfaces

Hiraku can be used through its Python API, CLI, and MCP server. These are interfaces to the same project state and core operations.

| MCP capability | Role in Hiraku |
| --- | --- |
| Resources | Read project state, records, Programs, and contexts when exposed by the server. |
| Tools | Call validated Hiraku operations. |
| Prompts | Start a user-selected workflow with arguments and references to relevant resources. |

MCP clients do not necessarily load every available resource automatically.
Prompts should include required Program and task content directly as well as
linking canonical resources. If content is provided only as a resource link,
read that resource before relying on it. Inspect the server's exposed tools
before relying on a particular operation.

MCP tools are validated Hiraku operations; they do not choose research
directions or define workflows. Use them for mutations when available. The CLI
provides another supported interface. Use `hiraku --help` and the relevant
subcommand's help to find its operations. Use the CLI when MCP is unavailable
or lacks the needed operation, provided it preserves the same validation.
Never switch interfaces to bypass a denied operation. If a
mutation call fails or its result is unclear, inspect project state before
retrying it through any interface.

MCP `launch_experiment` submits work asynchronously and returns a `launch_id`.
Poll `get_launch_status(launch_id)` until the launch is completed, failed, or
interrupted. Inspect its log tail before retrying a failed launch. Once the
process completes, query attributed runs and record the analysis. The CLI
`hiraku launch` command remains synchronous.

Programs are the single source of research workflow instructions. MCP prompts
are user-selected entry points that materialize the selected Program and
relevant task context; prompt resource links remain useful for discovery and
later reference. Harness startup prompts should identify user intent, task,
and selected Program without duplicating its workflow.

The harness controls execution permissions. Programs define workflow autonomy
and budgets, while Hiraku validates research state and provenance. MCP
elicitation or record fields do not grant operating-system or harness access.

Use `save_literature_review` to persist a task-linked Context with per-source
verification status. The Program prompt includes its Markdown directly and
links to the Context resource, so the next Program can consume the handoff even
when an MCP client does not automatically dereference resource links.
