# Agent workflows

The repository-local skill installed by `hiraku init` is the orchestration boundary. It is a router,
not a second persistence implementation.

## Guided

Inspect with `hiraku goal show`, `hiraku experiment show`, `hiraku runs --json`, and
`hiraku experiment tree`. Ask approval before each lifecycle mutation, then call the corresponding
CLI command. Keep source edits and commits on the automatically checked-out Experiment branch.

## Auto

Obtain approved limits, then follow the repository-local `auto.md` cycle with `hiraku auto context`,
`baseline`, `proposal create`, `next`, `implement`, the normal launcher, and `analyze`. The core owns
branch creation, implementation commits, Run creation/finalization, metric ingestion, and
conclusions. An unattended runtime can implement the same cycle through `auto loop --adapter`. The
agent must stop on scope, protocol, policy, permission, or resource-bound changes.

## Launch/analyze

Launch only a clean committed Experiment. Read the resulting Run rows and provenance, compare with
the Goal baseline, and record an Experiment verdict with `hiraku experiment conclude`. Do not edit
`.hiraku/state.db` or generated reports directly.
