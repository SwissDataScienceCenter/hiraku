# Relation to the other PoC branches

This branch was written independently of `feat/poc-implementation-1` and `-2` and shares no file with
them. It branches from `main` and adds only new paths.

Independent does not mean incompatible. Where those branches have already settled a convention, this
one adopts it deliberately, so the two halves compose rather than compete: they implement the
experiment/run mechanics, this specifies the research process over them. The overlaps below are
chosen; the differences are choices too, and both are listed so a reviewer can see which is which.

Read from the `-2` tree on 2026-08-24. If that branch moves, this document needs re-checking.

## Deliberate overlap — kept identical so the pieces compose

| Convention | Why it is kept |
|---|---|
| `experiments/<id>/experiment.md` with YAML front matter `id`, `branch`, `mode` | The three keys stay required and unchanged. Every record on this branch parses under their existing record parser, which validates exactly these and carries the rest through as metadata. The lineage keys ride along as extra front matter and are ignored by code that does not know about them. |
| `launch.yml` with `entrypoints: {<name>: {command: [argv…], cwd}}` | Adopted verbatim, argv arrays and all. Named entrypoints are what let a node's runs be re-launched from a recorded commit, which the promotion gate depends on. |
| `policy.md` per experiment, `guided` and `auto` | Adopted as-is, including that it guides agent behaviour and is not a security boundary. This branch adds one rule on top — a child may narrow its parent's policy, never widen it — which is the single-node rule extended to a tree. |
| Experiment IDs matching `^[a-z0-9]+(?:-[a-z0-9]+)*$` | Same rule, one flat namespace. Goal and proposal IDs use it too. |
| `.hiraku.yml` with `experiments_dir` and `tracking: {backend, path}` | Same shape. `research/` is found by convention beside `experiments/`, so no config key is required for it — though one would be tidier if this is adopted. |
| One project-level append-only CSV/TSV run table, requiring `experiment_id` and `commit` | Same table, same required columns. Everything this branch adds is optional columns, which their table reader already preserves verbatim. |
| Reproducible (`--commit`, isolated worktree) versus non-reproducible (working-tree) launches | Not just adopted — depended on. The promotion gate's rule that a working-tree run may never be promoted only means something because that distinction already exists and is enforced at launch. |
| A repository-local agent skill: one router, focused references loaded on demand | Same shape. The four references here are additions to that router, not a replacement for it. |

The practical test of all this: the records in `examples/natix-vo/` and `examples/worked-tree/` are
intended to load under their CLI unchanged, and the extra run-table columns to survive
`hiraku runs`. That has not been executed — their branch is not checked out here — so treat it as a
design intent to verify, not a verified fact.

## Differences

### 1. The lineage layer — the main one

Their `EXPERIMENT_LINEAGE.md` sets out six candidate models (linear, linear-with-discard, branch,
tag, custom refs, jj) and picks none; the implementation plan defers the data model and its naming
"until the design discussion resumes".

This branch decides it: **a sub-experiment is an experiment with a `parent`.** No third entity,
arbitrary depth, and lineage declared in front matter rather than derived from the commit graph.
Their document opens by aiming to leverage the commit graph; this branch argues against that
specifically — Git ancestry records what code something was built on, research lineage records which
question it responded to, and the two disagree routinely. The full argument, and verdicts on each of
their six candidates, is in [README.md](README.md).

One concrete finding from that analysis is worth flagging on its own: their sketch of hierarchical
branch names (`experiment/{exp}/add-batch-norm`) cannot work, because `refs/heads/exp/foo` and
`refs/heads/exp/foo/bar` cannot coexist — a loose ref is a file and cannot also be a directory. Branch
names have to stay flat.

### 2. A goal level above the experiment

They have no equivalent. This branch adds `research/goals/<id>.md`, which owns the evaluation
protocol — datasets, splits, metrics, seeds — for every experiment beneath it.

This is what makes a tree of comparisons mean anything. Their design deliberately does not certify
that metrics are comparable across experiments, which is correct for the CLI; something has to, and
the goal is where that responsibility goes.

### 3. A specified research process, not just a data model

Their branch specifies structure and mechanics precisely and leaves what the agent should *do* to the
skill's prose. This branch specifies the process: three failure classes, a six-condition test for
whether a sub-question is worth investigating, explicit stop conditions, and a five-condition
promotion gate. See [research-loop.md](research-loop.md).

### 4. A curated leaderboard, distinct from `hiraku runs`

Their plan defers project-wide leaderboards. `research/leaderboard.md` here is not a ranking of runs
— `hiraku runs --sort` already does that — but a per-goal record of results that passed a gate, with
their evidence, append-only. Different object, different question.

### 5. Literature-derived hypotheses

Their README lists the outer loop as out of scope. This branch includes a restricted form: reading
produces proposals in `research/proposals/`, never experiments, and a literature proposal needs a
human decision even in `auto` mode.

### 6. A dashboard

Their plan defers dashboards. `tools/hiraku_dash.py` is a read-only static generator, deliberately
outside the package and with no CLI dependency, so it can be dropped or replaced without touching
anything else.

### 7. Environment provenance

Their plan states that the exact persistence fields "will be finalized with the intermediate lineage
model" and defers `launch.require_reproducible`. Since this branch proposes that lineage model, it
proposes those columns too, plus which values `launch` should inject rather than leaving each project
to invent them. See [environment.md](environment.md).

### 8. Durable worktrees for parallelism

They already use a temporary worktree internally for a commit-backed launch. This branch proposes the
same primitive used durably — one worktree per active experiment, kept for the life of the node — so
siblings can run at once with conflicting dependencies. Same mechanism, different lifetime.

### 9. `program.md` is not adopted

Their plan moves to four-file template bundles: `experiment.md`, `program.md`, `policy.md`,
`launch.yml`, where `program.md` holds the agent workflow or research algorithm per experiment.

This branch does not use it, and the disagreement is real rather than an oversight. The process here
is meant to be the *same* for every node — that is what makes verdicts comparable and the tree
auditable — so it belongs in the skill references and the goal, not copied into each experiment where
it can drift per node. Their design gains flexibility, in that an experiment can carry a bespoke
algorithm; this one gains uniformity. If the bundles land, the reconciliation is probably that
`program.md` references a shared workflow by name rather than restating it.

### 10. No `src/` code here

They have a working CLI with tests and CI. This branch writes none, on purpose, so it can be argued
about before anything is built. [README.md](README.md) lists what it implies for the CLI.

## If both are merged

File paths are disjoint, so there is no textual conflict. Two things would need reconciling:

- **Where templates live.** This branch keeps them in `templates/` at the repository root as
  reviewable reference copies; their design installs them into a project via the packaged assets.
  Theirs is the right mechanism — these should become another template bundle rather than a second
  location.
- **`program.md`**, per difference 9.

Nothing else overlaps. The lineage keys, `research/`, the process documents, the skill references, and
the viewer are all additive.
