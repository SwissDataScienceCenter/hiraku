# Hiraku autoresearch design

The central distinction is:

```text
Goal → Experiment → Run
```

An Experiment is a concrete implementation attempt. Its `parent_id` expresses the research question
it responds to; Git ancestry is only source provenance. A Run evaluates one exact committed source
state. Guided and autonomous workflows use the same services and database.

Persistence is isolated behind SQLite repositories under `hiraku.infrastructure.sqlite`; application
services own lifecycle rules and transactions, while repositories perform row access.

## Core capabilities

| Area | Description |
|---|---|
| Goal protocol | Metric, direction, target, evaluation protocol, constraints, and baseline commit |
| Research lineage | Explicit `experiments.parent_id`, rendered by the CLI and TUI |
| Autonomous proposals | Proposals are admitted into ordinary Experiments |
| Evidence and failures | Runs and lifecycle events retain execution and result history |
| Projections | Reports and TUI query SQLite; guarded TUI actions call application services |

Read [cli.md](cli.md) for the executable walkthrough, [agent-protocol.md](agent-protocol.md) for
runtime integration, [data-model.md](data-model.md) for storage, and
[research-loop.md](research-loop.md) for guided/auto behavior.

For package boundaries and extension guidance, see [architecture.md](../architecture.md).
