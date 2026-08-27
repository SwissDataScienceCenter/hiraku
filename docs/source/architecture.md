# Source architecture

Hiraku is organized around one lifecycle core and several adapters. The package layout keeps
research rules independent from the CLI, Textual, Git, and SQLite details:

```text
hiraku/
├── application/       # Goal, Experiment, Run, analysis, lineage, and proposal services
├── orchestration/     # Autonomous loops, agent protocol, and project-context drafting
├── interfaces/        # CLI, TUI, and report projections
│   └── commands/       # Command operations grouped by lifecycle area
├── infrastructure/
│   └── sqlite/         # SQLite repositories and persistence implementation
├── database.py        # SQLite connection, schema, migrations, and transactions
├── git.py             # Git source-control adapter
├── launcher.py        # Validated process execution and Run finalization
├── models.py          # Lifecycle DTOs
└── app.py             # Composition root and dependency wiring
```

## Dependency rules

The dependency direction is inward:

```text
interfaces / orchestration
          ↓
application services
          ↓
domain models and validation
          ↓
SQLite, Git, manifests, and processes
```

Application services own lifecycle validation and transaction boundaries. SQLite repositories contain
SQL and row access; they do not decide whether an operation is allowed. Orchestration decides what to
do next but calls application services; it does not write SQLite rows or create Git branches directly.
Interfaces format results and collect user input. SQLite remains the authoritative lifecycle store;
reports and the TUI are projections (the TUI may invoke explicit lifecycle actions through services).

## Compatibility façades

The original flat module paths remain importable for the v0.1 API:

```text
hiraku.services  → hiraku.application.services
hiraku.auto      → hiraku.orchestration.autonomous
hiraku.agent     → hiraku.orchestration.agent_protocol
hiraku.context   → hiraku.orchestration.context
hiraku.cli       → hiraku.interfaces.cli
hiraku.tui       → hiraku.interfaces.tui
hiraku.reports   → hiraku.interfaces.reports
```

New code should import from the named subpackages. The façades allow existing skills, tests, and
third-party integrations to migrate without a flag day.

## Extension points

The application boundary now defines small structural contracts in
`hiraku.application.ports`:

- `StateStore` for connections and transactions;
- `SourceControl` for Git-like repository operations;
- `LauncherPort` for validated execution;
- `AgentAdapter` for vendor-neutral agent transport.

The current PoC supplies concrete SQLite, Git, process-launcher, and JSON-stdio implementations.
Service-level tests can use fakes that satisfy these protocols, while SQLite/Git integration tests
exercise the real adapters. This keeps future VCS or tracking integrations from leaking vendor-specific
concepts into Goal, Experiment, or Run semantics.
