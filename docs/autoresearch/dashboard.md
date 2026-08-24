# The dashboard

Markdown is the memory. The dashboard is a **projection** of it — generated, disposable,
regenerable, and never a place where information originates. Anything typed into a viewer instead of
a record is lost on the next regeneration, so the viewer is deliberately read-only.

`hiraku-auto dash` reads a project and writes one self-contained HTML file; `hiraku-auto ui` serves the same data as a Streamlit app.

```bash
hiraku-auto --project examples/natix-vo dash -o dashboard.html
```

No dependencies beyond the standard library (PyYAML is used if importable, with a fallback parser
for the front-matter subset defined in [data-model.md](data-model.md)). No network access at
runtime — CSS and JS are inlined, so the file opens from disk and can be attached to a review.

## What it shows

- Every goal, and beneath it the experiment tree, from `parent` links.
- Node status and verdict, by colour and label — a subtree that refuted its expectation is legible at
  a glance, which is the thing no `ls` of a directory gives you.
- `kind` and `origin` per node, so the agent's contribution and the researcher's are separable
  without reading records.
- On selecting a node: expectation, findings, conclusions, its child list, and its runs from the run
  table with their provenance and metrics.
- Promoted results per goal, and open proposals awaiting a decision.
- Which nodes are missing an expectation, or concluded without a verdict — the two errors that
  quietly make a tree unreadable.

## What it must not become

- **Not a control surface.** No launching, no editing, no approving. Approval happens in the
  conversation and is recorded in the files; a button in a viewer would create authority with no
  audit trail, which is exactly what `policy.md` exists to prevent.
- **Not a metrics platform.** No training curves, no artefact browsing, no run comparison plots.
  MLflow and W&B do that, and the README keeps run-artefact ingestion out of scope. The dashboard
  shows a run's *row*, and links out.
- **Not a second source of truth.** If the HTML and the markdown disagree, the markdown is right and
  the generator has a bug. It is committed to the branch for reviewability, and can be regenerated
  from scratch at any time.

## Data contract

The generator reads only:

| Path | Used for |
|---|---|
| `.hiraku.yml` | `experiments_dir`, `tracking.path` |
| `research/config.md` | project title, bounds shown in the header |
| `research/goals/*.md` | goals, `primary_metric`, `direction`, `target` |
| `experiments/*/experiment.md` | front matter and named body sections |
| `research/proposals/*.md` | the pending queue |
| `research/leaderboard.md` | promoted entries |
| the configured run table | run rows, joined on `experiment_id` |

It writes exactly one file and never touches the project. Being read-only is what makes it safe to
run in a hook or in CI.

The generator consumes `tree.to_dict` — the same snapshot the terminal, the agent and the
Streamlit app read — so tree construction and validation live in one place and no viewer can
disagree with `hiraku-auto check`.

## Route to a live server

The static file is the right first step because it forces the data contract to be explicit, and
because a file you can email or attach to a merge request is what a reviewer actually wants. A
server is a strict addition once that contract holds:

1. `hiraku dash` — the generator behind a command, writing to a configured path.
2. `hiraku dash --serve` — the same render, served locally, watching `experiments/`, `research/`, and
   the run table, re-rendering on change and pushing over a websocket. Useful during a long auto
   loop, where the point is watching the tree grow.
3. Left for later: a shared instance. That means auth, access control over research in progress, and
   a deployment story — a different project from a local viewer, and not one to start by accident.

The generator is structured with this in mind: parsing produces a plain dictionary, and rendering is
a pure function of it. A server reuses the first half and re-renders on change.
