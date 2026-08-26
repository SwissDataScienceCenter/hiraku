# An auto-research tool for Hiraku — design

Branch: `feat/poc-implementation-zou`. Base: `main`. Independent of the other PoC branches.

The README on `main` describes Hiraku's conventions — one `experiment.md` per experiment linking
documentation, a Git branch, `launch.yml` entrypoints, and runs in a tracking backend, with a
`guided`/`auto` mode distinction. It leaves the *research process* over those experiments
undefined: how a goal decomposes into parallel hypotheses, what happens when a result disappoints,
when a sub-question is worth asking, and how any of it stays legible once one experiment has spawned
twenty. This branch specifies that layer and provides a viewer over it.

A working Python package (`hiraku_auto`) with a CLI, an agent driver, a Streamlit interface and a
test suite, plus the specification behind it and two seeded projects.

**Start here: [cli.md](cli.md).** That is the ten-minute walkthrough and the command reference.
The documents below are the reasoning — read them when you want to argue with a decision rather
than use the tool.

## The five requirements, and where each is answered

| # | Requirement | Answer | Where |
|---|---|---|---|
| 1 | A goal for the task, with a `config.md` for the setting / goal / environment | Two files: `research/config.md` for the project contract (environment, bounds, promotion rules), and one `research/goals/<id>.md` per research question, owning the evaluation protocol that makes comparisons under it meaningful | [data-model.md](data-model.md), [`templates/config.md`](../../src/hiraku_auto/templates/config.md), [`templates/goal.md`](../../src/hiraku_auto/templates/goal.md), [goal-drafting.md](goal-drafting.md) |
| 2 | Parallel experiments; multiple hypotheses → multiple experiments | Several experiments share a `goal`. Each is an ordinary Hiraku experiment with its own branch and runs; "parallel" is several of them active at once, separated on disk by Git worktrees | [data-model.md](data-model.md), [environment.md](environment.md) |
| 3 | Optionally, the agent reads literature to find new hypotheses, designs experiments, and asks whether to test them | Reading produces **proposals** in `research/proposals/`, never experiments. A proposal states the source's claim separately from the agent's inference, argues why it transfers, answers the investigation test, and argues the case *against* itself. Literature proposals need a human decision even in `auto` mode | [hypothesis-discovery.md](hypothesis-discovery.md), [`templates/proposal.md`](../../src/hiraku_auto/templates/proposal.md) |
| 4 | Good result → `leaderboard.md`; bad result → analyse, decide whether a parameter or setting change could fix it, possibly splitting into more experiments; check whether investigating is meaningful; if so ask; otherwise record the failed attempt | A five-condition promotion gate for the first half, and for the second a triage protocol: classify the failure into one of three classes, then apply a six-condition investigation test. Any condition failing is the loop's stop signal, and the condition that failed is what gets recorded | [research-loop.md](research-loop.md), [`templates/leaderboard.md`](../../src/hiraku_auto/templates/leaderboard.md) |
| 5 | Branching for different experiments | One flat branch `exp/<id>` per experiment, plus a durable worktree per active experiment so siblings can run at the same time with their own environments | [environment.md](environment.md) |
| ★ | Tracking: experiments split into sub-experiments, so the record ends up a tree. Markdown as the memory, plus a clickable interface showing status, connections, and results | A sub-experiment **is** an experiment, with `parent` pointing at the node whose result it responds to — so the tree is arbitrary-depth recursion on the object that already exists. Markdown stays the only source of truth; both viewers are regenerable projections | [data-model.md](data-model.md), [dashboard.md](dashboard.md), `hiraku-auto dash`, `hiraku-auto ui` |

Each row is implemented, not only specified. The code that generates the records is
`hiraku_auto/scaffold.py`; the tree is `hiraku_auto/tree.py`; the triage and promotion rules are
`hiraku_auto/agent.py` and `hiraku_auto/promote.py`; the agent call is `hiraku_auto/agent.py`.

## The shape of it

```
goal   vo-backend-selection             the question, its metric, its evaluation protocol
 ├── experiment  occany-baseline         branch exp/occany-baseline, runs, verdict
 │    ├── experiment  vggt-omega              parent: occany-baseline
 │    ├── experiment  vggt-long               parent: occany-baseline
 │    └── experiment  rig-camera-vo           parent: occany-baseline, verdict: refutes
 │         ├── experiment  rig-calibration-audit    parent: rig-camera-vo, kind: diagnostic
 │         └── experiment  rig-overlap-geometry     parent: rig-camera-vo, kind: diagnostic
 └── ...
```

Four levels, but only one of them is a new kind of thing:

- **Goal** — a research question. One small file. Owns the evaluation protocol.
- **Experiment** — unchanged from the README's definition. Gains three optional front-matter keys:
  `goal`, `parent`, `kind`.
- **Run** — unchanged. A row in the tracking backend, with provenance columns proposed in
  [data-model.md](data-model.md).
- **Environment** — columns on the run row, not a separate artefact.

### Two decisions worth arguing about

**A sub-experiment is an experiment, not a new entity.** The alternative is a third noun —
`experiments/<id>/hypotheses/<hyp>/`. Recursion wins on depth (a refuted hypothesis spawns a
diagnostic, which spawns an ablation; two fixed levels cannot hold that, and "try A, B, C, D then
combine the ones that work" needs a node with two parents), on cost (`exp create --parent <id>` is
one flag, against a new record type, parser, ID space, branch convention, CLI surface and tests),
and on uniformity — `exp show`, `runs`, the skill, the viewer and a pull-request diff all keep one
shape to understand. The price is that "experiment" now spans a multi-week baseline and a one-run
threshold sweep; `kind` carries that, which is honest, because the difference was always about
intent rather than about being a different kind of object.

**Lineage is declared, not derived from the commit graph.** Tempting to read the tree off Git, since
runs already launch from commits. Don't: Git ancestry answers *what code was this built on*, research
lineage answers *which question was this asked in response to*, and they come apart constantly. A
diagnostic child that investigates why its parent failed usually branches from the **same** commit —
it adds instrumentation, not a change of method, so Git sees a sibling where research sees a child. A
child that supersedes its parent is often rebuilt from the baseline, so Git sees no relationship at
all. A rebase, squash or cherry-pick rewrites ancestry without changing what was asked. Declaring
`parent` costs one line and survives every Git operation; deriving it makes the research record a
hostage to routine history maintenance.

Git stays authoritative for the thing it is good at — reproducibility. The commit a run executed is
recorded per run, and the two graphs get cross-checked rather than conflated.

**A consequence for branch names.** `refs/heads/exp/foo` and `refs/heads/exp/foo/bar` cannot both
exist: loose refs are files, so a ref cannot also be a directory. Naming a child branch after its
parent (`exp/occany-baseline/vggt-omega`) therefore breaks as soon as the parent has its own branch —
which it always does. Branch names stay flat, `exp/<child-id>`, and the hierarchy lives in `parent`.
Experiment IDs are globally unique, so this costs nothing.

## Documents

| Document | Settles |
|---|---|
| [commands.md](commands.md) | **Every command and flag**, generated from the parser |
| [cli.md](cli.md) | **How to run it**: install, the ten-minute walkthrough, how metrics are reported, how the agent is called, the loop, the promotion gate, and every command |
| [data-model.md](data-model.md) | Front matter for goals and experiments, ID rules, run-table provenance columns, why `parent` rather than nested directories, and what is deliberately not recorded |
| [research-loop.md](research-loop.md) | Requirement 4 in full: the three failure classes, the six-condition investigation test, who decides in each mode, how a terminal failure is recorded, and the promotion gate |
| [goal-drafting.md](goal-drafting.md) | The first step: giving the agent the project's own files (`CLAUDE.md`, README, docs) so it drafts the goal record and its evaluation protocol, and marks with `**[UNSET: ...]**` every value it could not ground rather than guessing |
| [hypothesis-discovery.md](hypothesis-discovery.md) | Requirement 3: proposals from reading, treating retrieved text as evidence rather than instruction |
| [environment.md](environment.md) | Requirements 1 and 5: the reproduce-minimum capture set, and worktrees for parallel separation |
| [dashboard.md](dashboard.md) | The viewer's data contract, what it must never become, and the route to a live server |
| [relation-to-other-branches.md](relation-to-other-branches.md) | Where this branch deliberately keeps the conventions of `feat/poc-implementation-1`/`-2` so the pieces compose, and the ten places it diverges |
| [agent-workflows/](agent-workflows/) | Skill references: [create a node](agent-workflows/create-child.md), [triage a failure](agent-workflows/triage-failure.md), [discover hypotheses](agent-workflows/discover-hypotheses.md), [run a bounded loop](agent-workflows/auto-loop.md) |

## Projects on this branch

- [`examples/natix-vo/`](../../examples/natix-vo/) — **seeded for the two live questions**: which VO
  backend works on NATIX video, and whether DINOv3 features help under the Mapillary↔NATIX domain
  gap. Real questions, real tree structure, **no results** — the run table is empty and every
  Findings section is a marked gap for you to fill from work already done. Nothing here is invented.
- [`examples/worked-tree/`](../../examples/worked-tree/) — a fully populated **synthetic** tree used
  to exercise the viewer and to show the loop's states end to end: a refutation, an inconclusive
  verdict, a rejected investigation, a passing promotion, and a promotion refused for
  non-reproducibility. Every number in it is fabricated and labelled as such.

## Relationship to guided and auto mode

Unchanged from the README. The tree adds one rule: **a child inherits its parent's mode and policy,
and may narrow but never widen it.** An auto node may spawn auto children inside its budget; a
guided node's child is guided. Otherwise a program could grant itself authority its policy withheld
by creating a child.

## What is built, and what is not

Built: `init`, `goal`, `exp` (with `--parent`, `--goal`, `--kind`), `tree`, `check`, `sync`,
`launch` (both forms, with provenance columns written by the launcher), `runs`, `agent`
(design / analyze / triage / discover), `next`, `loop`, `promote` (all five gate conditions),
`proposal`, `worktree`, `dash`, `ui`. 79 tests, no required dependencies.

Not built, in rough order of value:

1. **Commit-ancestry cross-check** — `check` validates the declared tree, but does not yet report
   where declared parentage and git ancestry disagree. That divergence is a useful review signal
   rather than an error, and it is the one part of the lineage argument still unimplemented.
2. **Expectation-before-run enforcement** — the rule is stated and the agent refuses to triage
   without an expectation, but nothing records *when* it was written, so a retrofitted expectation
   is not detectable.
3. **`dash --serve`** — the static page exists; a watching server does not.
4. **Annotated tags per launched commit** — cheapest way to keep a commit that produced a recorded
   result reachable after its branch is deleted.
5. **MLflow / W&B backends** — the table is the only tracking backend.
6. **Multi-parent `combination` nodes** — the data model has `parents`, and `check` and the tree
   handle it, but no CLI flag creates one.
