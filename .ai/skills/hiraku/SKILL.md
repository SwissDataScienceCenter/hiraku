---
name: hiraku
description: Use Hiraku as the research control plane for data-science and machine-learning work.
---

# Hiraku

Hiraku connects research goals, records, source code, execution, evidence, and conclusions.

The user's request and harness control execution scope and permissions. A
selected Hiraku Program defines the research workflow, including its stages,
budgets, and stopping conditions. This Skill defines how to operate Hiraku.
Hiraku validates and persists research state, provenance, and tracking; the
harness owns reasoning, code editing, iteration, and user interaction.

Follow the user's scope and preserve existing work and provenance. A Program
does not grant operating-system or harness permissions.

## Operating contract

1. Read the selected Program before executing its workflow. The Program owns
   research steps and autonomy policy; do not recreate them in this Skill or in
   a harness startup prompt.
2. Use Hiraku MCP for exposed resources and validated operations. A resource
   link may not have been read by the harness; inspect the returned prompt or
   read the resource explicitly before relying on its content.
3. If MCP is unavailable or lacks an operation, use the equivalent Hiraku CLI
   command when it applies the same validation requirements. Do not use a
   fallback to bypass a denied operation.
4. Do not edit persisted record files by hand to bypass Hiraku. If a mutation
   fails or its result is unclear, inspect current state before retrying or
   switching interfaces. Never create the same record through both MCP and CLI.
5. Use Hiraku launch and tracking mechanisms for experiments. Preserve run
   attribution; do not disable tracking or invent provenance.
6. Follow the selected Program's autonomy policy and budgets. The harness
   decides whether an operation is permitted; Hiraku does not store or grant
   execution authorization.

Before creating state, inspect the project and existing records. For commit
provenance, keep research records separate from experiment implementation
commits; see the Record model reference.

Read these references only when their concepts matter to the current task:

- [Record model](references/record-model.md) for hierarchy, inheritance, and
  how records relate to tracked runs.
- [Interfaces](references/interfaces.md) for the roles of MCP resources, tools,
  prompts, and the Hiraku CLI.
