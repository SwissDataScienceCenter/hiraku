# Relation to the earlier autoresearch PoC

The earlier autoresearch branch explored a broad autoresearch layer over Markdown experiment records, durable
worktrees, a separate `hiraku-auto` package, promotion gates, and a dashboard. This branch starts
from `main` and folds the durable parts into one smaller shared core.

| Earlier convention | Unified v0.1 choice |
|---|---|
| `research/goals/<id>.md` as state | SQLite `goals` row; Markdown reports are projections |
| `experiments/<id>/experiment.md` | SQLite `experiments` row; source branches contain implementation |
| flat experiment branches | `hiraku/<goal>/<experiment>`, automatically checked out |
| `parent` lineage | `parent_id`, validated independently of Git ancestry |
| proposal files | `proposals` table and `hiraku auto proposal` |
| CSV/table run backend | SQLite Runs with JSON/CSV export |
| `hiraku-auto` package | one `hiraku` package and executable |
| durable worktree per active node | normal branch by default; temporary worktree only for `--commit` |
| promotion gate | explicit Experiment verdict; promotion deferred |
| dashboard/Streamlit | read-only Textual TUI plus generated reports |

The conceptual research-process material is compatible: goals define comparability, proposals remain
separate from experiments, parent links describe research response, and every failure should remain
auditable. The implementation details above are the authoritative current behavior.
