---
name: hiraku
description: Use Hiraku as the research control plane for data-science and machine-learning work.
---

# Hiraku

Hiraku connects research goals, records, source code, execution, evidence, and conclusions.

The coding harness owns reasoning, repository inspection, code editing, looping, and user interaction. Hiraku owns authoritative research state, validation, commits, launches, attribution, and recorded conclusions.

Follow the user's scope and preserve existing work and provenance.

MCP and the `hiraku` CLI are both authoritative Hiraku interfaces. Prefer the
MCP operation when it exists; use the corresponding CLI command when it does
not. Do not edit record files by hand to bypass Hiraku.

Before creating state, inspect the project and existing records. If a create
operation fails or its result is uncertain, inspect current state before
retrying or switching interfaces. Never create the same record once through
the CLI and again through MCP.

For a named workflow, read and follow its Hiraku Program. Use `interactive` for
guided step-by-step work and `autoresearch` for bounded autonomous
experimentation.

Read these references only when their concepts matter to the current task:

- [Record model](references/record-model.md) for hierarchy, inheritance, and
  how records relate to tracked runs.
- [Interfaces](references/interfaces.md) for the roles of MCP resources, tools,
  prompts, and the Hiraku CLI.
