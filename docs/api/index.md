# Python API

Hiraku exposes validated Pydantic models and small service APIs. Constructors
validate their input immediately; persistence is handled through explicit paths.

- [Configuration](config.md) — discover and persist project configuration.
- [Records](records.md) — work with validated Markdown-backed records.
- Data models:
    - [Task](task.md)
    - [Experiment](experiment.md)
    - [Run](run.md)
    - [Metric](metric.md)
    - [Entrypoint](entrypoint.md)
- [Tracking](tracking.md) — configure and query tracking backends.
- [Launching](launch.md) — resolve and execute entrypoints.
