# TUI and reports

The current viewer is a read-only Textual TUI:

```bash
uv sync --extra tui
hiraku tui
```

It renders one computed tree from the shared query services: Goal metric/direction, baseline and
hypothesis Experiments, parent/child relationships, statuses, verdicts, commits, Run metrics,
parameters, provenance, and auto-origin proposals. It has no launch, edit, approve, conclude, or
abandon controls.

Reports are generated projections:

```bash
hiraku report generate <goal-id>
```

This writes Markdown, JSON, and CSV below ignored `.hiraku/reports/`. The Markdown and JSON include a
projection warning; the JSON includes lineage, Runs, proposals, and lifecycle events. The TUI and
reports consume the same core queries, so a viewer cannot become a second source of truth.

The static projections are the v0.1 endpoint. A live browser dashboard, server, charts, artifact
browser, and mutation controls are intentionally deferred.

