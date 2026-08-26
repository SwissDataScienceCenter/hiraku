# Hiraku autoresearch design

This document carries forward the research-process ideas developed in the earlier autoresearch PoC and maps them
to the unified v0.1 implementation on this branch.

The central distinction is:

```text
Goal → Experiment → Run
```

An Experiment is a concrete implementation attempt. Its `parent_id` expresses the research question
it responds to; Git ancestry is only source provenance. A Run evaluates one exact committed source
state. Guided and autonomous workflows use the same services and database.

## Retained autoresearch ideas

The following ideas are retained:

| Idea | Current implementation |
|---|---|
| A Goal owns the comparison protocol | `goals` table: metric, direction, target, protocol, constraints, immutable baseline commit |
| Parent links form arbitrary-depth research trees | `experiments.parent_id`; `hiraku experiment tree` and the TUI render it |
| Proposals are not experiments | `proposals` table; `hiraku auto proposal ...` admits ordinary Experiments |
| A failed result needs classification and an auditable next step | `events` records implementation, execution, and research-result failures |
| Environment facts belong with evidence | Run provenance records Python, platform, accelerator, lockfile digest, and container identity when available |
| Viewers are projections | Reports and the TUI read SQLite and cannot mutate lifecycle state |

## Deliberate v0.1 differences

This branch is intentionally smaller and operationally simpler than the earlier autoresearch prototype:

- `.hiraku/state.db` is authoritative; Markdown is generated reporting, not lifecycle state.
- Git branches are created and checked out automatically. Durable per-Experiment worktrees are
  deferred; `hiraku launch --commit` uses only a temporary detached worktree.
- There is one `hiraku` executable. Guided commands live at the root; bounded orchestration is
  under `hiraku auto`.
- The baseline is an Experiment with `kind=baseline`, but there is no promotion/current-best pointer.
- There is no automatic statistical significance engine, MLflow/W&B adapter, scheduler, dashboard
  server, remote collaboration, or multi-parent combination node.
- The CLI auto loop uses an approved fixed implementation command from `launch.yml`. A skill-aware
  agent can use `auto next` for richer implementation work.

Read [cli.md](cli.md) for the executable walkthrough, [data-model.md](data-model.md) for storage,
and [research-loop.md](research-loop.md) for guided/auto behavior.
