# Hiraku interfaces

Hiraku can be used through its Python API, CLI, and MCP server. These are interfaces to the same project state and core operations.

| MCP capability | Role in Hiraku |
| --- | --- |
| Resources | Read project state, records, Programs, and contexts when exposed by the server. |
| Tools | Call validated Hiraku operations. |
| Prompts | Start a user-selected workflow with arguments and references to relevant resources. |

MCP clients do not necessarily load every available resource automatically.
Read the resource needed for the task, and inspect the server's exposed tools
before relying on a particular operation.

The CLI provides another supported interface. Use `hiraku --help` and the
relevant subcommand's help to find its current operations. If MCP does not
expose a needed operation, use the corresponding CLI command rather than
editing persisted files directly. If a mutation call fails or its result is
unclear, inspect project state before retrying it through any interface.

Programs are Hiraku Markdown artifacts. MCP prompts can offer convenient
launchers for Programs, but the Program file remains the reusable procedure.
