# Testing architecture

Hiraku separates fast service-level tests from adapter integration tests.

## Service-level tests

Application and orchestration tests use small fakes that satisfy the contracts in
`hiraku.application.ports`:

```text
StateStore       connections and transactions
SourceControl    repository/source operations
LauncherPort     validated execution
AgentAdapter     request/response transport
```

These tests exercise lifecycle rules without requiring a real Git checkout, external process, or
agent runtime. They are appropriate for validation, parameter immutability, lineage, approval, and
orchestration behavior.

## Integration tests

SQLite/Git tests use temporary repositories and the concrete adapters. They verify migrations,
foreign keys, WAL-backed transactions, branch operations, exact commit provenance, temporary
worktrees, launcher behavior, and durable Runs for failures and interruptions.

Both layers are required: fakes keep the core fast and deterministic, while integration tests ensure
the concrete persistence and source-control adapters satisfy the same contracts.
