# Python API

Hiraku exposes validated project, record, and run models. Records are persisted
through the project-scoped `Vault`; tracking adapters return normalized `Run`
objects.

- [Project](project.md) — create a project and install its resources.
- [Jobs and execution](../../guides/jobs.md) — prepare native PSI/J jobs, submit, and retrieve final status.
- [Records and Vault](records.md) — work with validated Markdown-backed records and their collection.
- [Tracking](tracking.md) — configure and query tracking backends.
- [Workflows](workflows.md) — define LangGraph workflows and manage their sessions.
