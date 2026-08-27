# TUI and reports

The current viewer is a Textual TUI backed by the shared application services:

```bash
uv sync --extra tui
hiraku tui
```

It renders one computed tree from the shared query services: Goal metric/direction, baseline and
hypothesis Experiments, parent/child relationships, statuses, verdicts, commits, Run metrics,
parameters, provenance, and auto-origin proposals. Explicit key bindings can conclude, abandon,
retry, or delete eligible lifecycle records; these actions still go through the core services and
never edit SQLite directly.

Reports are generated projections:

```bash
hiraku report generate <goal-id>
```

This writes Markdown, JSON, and CSV below ignored `.hiraku/reports/`. The Markdown and JSON include a
projection warning; the JSON includes lineage, Runs, proposals, and lifecycle events. The TUI and
reports consume the same core queries, so a viewer cannot become a second source of truth.

Reports and the compact TUI provide the current review interface.
