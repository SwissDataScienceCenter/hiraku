# Python API

Hiraku exposes validated Pydantic models and small service APIs. Constructors
validate their input immediately; persistence is handled through explicit paths.

- [Configuration](config.md) — discover and persist project configuration.
- [Records](records.md) — work with validated Markdown-backed records.
- Data models:
    - [Record and BaseRecord](records.md)
    - [Run](run.md)
    - [Metric](metric.md)
    - [Job](job.md)
- [Tracking](tracking.md) — configure and query tracking backends.
- [Jobs](../../guides/jobs.md) — submit and inspect project executions.
- [Workflows](workflows.md) — author workflows and drive their sessions.
