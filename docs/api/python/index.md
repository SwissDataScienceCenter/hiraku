# Python API

Hiraku exposes validated Pydantic models and small service APIs. Constructors
validate their input immediately; persistence is handled through explicit paths.

- [Configuration](config.md) — discover and persist project configuration.
- [Project initialization](project.md) — create a project and install its resources.
- [Records](records.md) — work with validated Markdown-backed records.
- Data models:
    - [Record](records.md)
    - [Run](run.md)
    - [Metric](metric.md)
- [Tracking](tracking.md) — configure and query tracking backends.
- [Command execution](../../guides/execution.md) — run commands in the foreground.
- [Workflows](workflows.md) — define LangGraph workflows and manage their sessions.
