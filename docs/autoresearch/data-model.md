# Data model

Four levels. Only the middle one is a directory of records; the others are a small file, a
recursion, and a table row.

| Level | Lives in | Cardinality |
|---|---|---|
| Goal | `research/goals/<goal-id>.md` | one research question |
| Experiment | `experiments/<experiment-id>/experiment.md` | a tree per goal |
| Run | a row in the project run table | many per experiment |
| Environment | columns on the run row | one per run |

`experiments/` and the run table keep their current locations and meaning. `research/` is new and
holds everything above the experiment: the project contract, goals, agent proposals, and the
promoted-results board.

```
.hiraku.yml
.hiraku/runs.csv
research/
  config.md                       project contract: goals, environment, bounds
  leaderboard.md                  promoted results only
  goals/<goal-id>.md
  proposals/<proposal-id>.md      agent-proposed, awaiting a decision
experiments/<experiment-id>/
  experiment.md                   scope, expectation, findings, decision
  launch.yml                      entrypoints
  policy.md                       permissions and bounds
```

## Identifiers

Goal, experiment, and proposal IDs all use the existing experiment rule:
`^[a-z0-9]+(?:-[a-z0-9]+)*$`. One flat namespace per level, globally unique, never reused — a
retired ID stays retired so that a run row or a leaderboard entry never becomes ambiguous.

IDs name the *question*, not the position in the tree: `ransac-threshold-sweep`, not
`geometric-verification-child-2`. Position is `parent`, and position changes; a name that encodes it
becomes a lie the first time the tree is reorganised.

## Goal record

```markdown
---
id: same-place-verification
status: active            # active | achieved | abandoned | paused
primary_metric: verified_precision
direction: maximize       # maximize | minimize
target: 0.95              # optional; the number that would settle the question
---
```

Body sections: **Question**, **Why it matters**, **Success criterion**, **Evaluation protocol**
(datasets, splits, metrics — fixed before any run), **Out of scope**, **Open threads**.

`primary_metric` and `direction` exist so that "is this result better" has a project-level answer
rather than a per-conversation one. The evaluation protocol is written once, at the goal, and
inherited by every experiment beneath it; an experiment that needs a different protocol is
answering a different question and belongs under a different goal. This is the one piece of
governance that keeps a tree of comparisons meaningful — `hiraku runs` deliberately does not certify
that metrics are comparable, so the goal must.

## Experiment record

The first three keys are what the CLI requires and validates. Everything below is optional; a
record without them is still a valid experiment, it simply has no place in a tree.

```markdown
---
# existing, required
id: geometric-verification
branch: exp/geometric-verification
mode: guided

# new, optional
goal: same-place-verification
parent: retrieval-baseline
kind: hypothesis
status: concluded
verdict: supports
origin: researcher
expectation: "Two-view RANSAC on retrieved pairs removes most false positives at a small recall cost."
created: 2026-08-11
concluded: 2026-08-14
---
```

| Key | Values | Meaning |
|---|---|---|
| `goal` | goal ID | Which question this serves. Required on roots; inherited from `parent` when absent. |
| `parent` | experiment ID, or absent | Absent means a root of its goal's tree. |
| `kind` | `baseline` `hypothesis` `diagnostic` `ablation` `combination` | Intent. Drives triage and the dashboard's shapes. |
| `status` | `proposed` `approved` `running` `concluded` `abandoned` | Lifecycle. `proposed` means it exists as a record but is not authorised to run. |
| `verdict` | `supports` `refutes` `inconclusive` | Set only when `status: concluded`. About the *expectation*, never about whether the number went up. |
| `origin` | `researcher` `agent-diagnostic` `agent-literature` | Who proposed it. Makes the agent's contribution auditable and separable. |
| `expectation` | one sentence | What this node predicts, written **before** its first run. |
| `parents` | list of experiment IDs | Only for `kind: combination`; replaces `parent`. |

Body sections extend the existing template rather than replacing it — `Scope: hypothesis or task`,
`Implementation plan`, `Evaluation protocol`, `Findings`, `Conclusions`,
`Decisions and next steps` all keep their names and meaning. The additions are:

- **Expectation and how it could fail** — written before launching. The prose behind
  `expectation`, plus what result would count as refutation. A node that cannot state what would
  refute it is not an experiment.
- **Children** — a generated list of child IDs with their verdicts, so the record is readable
  without the dashboard.

### `verdict` is about the expectation

A node that predicted an improvement, was implemented correctly, and produced no improvement is
`refutes` — a successful experiment with a negative result. It is not a failure, and it is not
`inconclusive`. `inconclusive` is reserved for the case where the evidence cannot distinguish
between the expectation holding and not holding: too few seeds, a confound, a broken run.

Keeping these apart is what makes the tree navigable later. Collapsing them is how a research log
turns into a list of things that "didn't work" with no memory of which were actually settled.

## Why `parent` is a single key and not a nested directory

Nesting (`experiments/a/children/b/`) encodes the tree in paths. That makes reparenting a file move,
deep nodes have long paths, and every consumer must walk directories rather than read a field. A
flat directory with one `parent` key keeps IDs and paths stable for the life of the project, makes
reparenting a one-line edit with a clean diff, and lets the existing `experiments_dir` glob keep
working untouched.

The tree is therefore always *computed*: read every record, group by `goal`, link by `parent`.
Validation is cheap and should be a CLI check — every `parent` and `goal` resolves, no cycles, no
node whose `goal` disagrees with its parent's.

## Run table

`experiment_id` and `commit` are the minimum a row must carry to be traceable. The rest of the
provenance set is proposed here.

| Column | Required | Meaning |
|---|---|---|
| `experiment_id` | yes | Existing. |
| `commit` | yes | Existing. Executed source commit for a commit-backed run; base `HEAD` for a working-tree run. |
| `run_id` | yes | Unique per run. |
| `run_kind` | yes | `commit` or `worktree`. |
| `reproducible` | yes | `true` only for `run_kind: commit`. The plan already requires this distinction; this is the column that carries it. |
| `launched_at` | yes | UTC ISO-8601. |
| `entrypoint` | yes | The `launch.yml` entrypoint name. |
| `seed` | recommended | The seed that governs the run. |
| `python_version`, `lockfile_sha256`, `platform`, `accelerator` | recommended | See [environment.md](environment.md). |
| anything else | — | Preserved verbatim, as today. Metrics and parameters live here. |

Deliberately **not** a column: `hypothesis_id`. Because a hypothesis is an experiment,
`experiment_id` already identifies the node — adding a second lineage column would create two
sources of truth for the same fact and a way for them to disagree. Roll-up across a subtree is a
read-time join through `parent`, not a denormalised column.

Also deliberately not a column: anything about the tree's shape. The table records executions; the
records record structure. Duplicating structure into the table means every reparenting has to
rewrite history in an append-only file.

## What is intentionally left out

- **No status field on runs beyond what the process reports.** `launch` propagates the child exit
  status and does not verify that a row was written; a run that produced no row is visible as a
  gap, which is more honest than a synthesised "failed" row.
- **No per-node metric copies.** A node's numbers are always read from the table, filtered by
  `experiment_id`. A copy in the record is a copy that goes stale.
- **No cross-goal comparison.** Two goals may share a metric name and still be incomparable;
  `research/leaderboard.md` is therefore per-goal, matching the existing decision to keep
  cross-experiment leaderboards out of the PoC.
