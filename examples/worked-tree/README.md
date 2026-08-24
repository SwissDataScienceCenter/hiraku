# Worked tree — synthetic fixture

A fully populated tree, used to exercise the viewer and to show every state of the loop in
[`docs/autoresearch/`](../../docs/autoresearch/) end to end.

## This is synthetic

**Every record, run row, metric, and conclusion in this directory is fabricated.** It is not a
runnable project — there is no `pr` package and no data. It exists because a tree with results in it
demonstrates things an empty tree cannot: a refutation, an inconclusive verdict, a rejected
investigation, a passing promotion, and a promotion refused for non-reproducibility.

For the live research questions, see [`examples/natix-vo/`](../natix-vo/) — real questions, real tree,
no invented results.

## What it is here to demonstrate

| Feature | Where to look |
|---|---|
| A tree deeper than two levels | `retrieval-baseline` → `geometric-verification` → `ransac-threshold-sweep` |
| Parallel hypotheses under one goal | `geometric-verification` and `vggt-pose-consistency`, both children of the baseline |
| Multiple roots under one goal | `temporal-adjacency-exclusion` and `sequence-level-dedup` |
| `verdict: refutes` as a result, not a failure | `temporal-adjacency-exclusion` — sound implementation, rule genuinely does not work |
| `verdict: inconclusive` used correctly | `ransac-threshold-sweep` — names the competing explanation the evidence cannot separate |
| The investigation test stopping the loop | `ransac-threshold-sweep`, rejected on **proportionate**; the reasoning is in its Conclusions |
| The investigation test passing | `gps-radius-exclusion` and `depth-scale-drift`, both with all six conditions answered |
| An agent-proposed diagnostic awaiting a decision | `depth-scale-drift`, `origin: agent-diagnostic`, `status: proposed` |
| A literature proposal needing human sign-off | `research/proposals/sequence-level-dedup.md`, with a recommendation to defer |
| A non-reproducible run excluded from evidence | `r-0010`, `run_kind: worktree` |
| The promotion gate, passing | `geometric-verification` on `r-0003` with `r-0004` as reproduction |
| The promotion gate, refusing | `research/leaderboard.md`, "Not promoted, worth knowing" |
| A goal with no leader yet | `candidate-contamination` |
| `mode: auto` on a root, with a bounded policy | `temporal-adjacency-exclusion/policy.md` |
| Mixed metric columns in one run table | `verified_precision` and `contaminated_frac`, blank where inapplicable |

## The tree

```
same-place-verification            verified_precision ↑ target 0.95
└── retrieval-baseline             baseline    concluded  supports
    ├── geometric-verification     hypothesis  concluded  supports   ← promoted leader
    │   └── ransac-threshold-sweep ablation    concluded  inconclusive
    └── vggt-pose-consistency      hypothesis  running
        └── depth-scale-drift      diagnostic  proposed   (agent-diagnostic)

candidate-contamination            contaminated_frac ↓ target 0.02
├── temporal-adjacency-exclusion   baseline    concluded  refutes   (auto)
│   └── gps-radius-exclusion       diagnostic  approved   (auto, agent-diagnostic)
└── sequence-level-dedup           hypothesis  proposed   (agent-literature)
```

## View it

```bash
hiraku-auto --project examples/worked-tree dash -o dashboard.html
hiraku-auto --project examples/worked-tree ui
```

## Layout

```
.hiraku.yml                       unchanged from the current default shape
.hiraku/runs.csv                  run table with the proposed provenance columns
research/config.md                project contract: environment, bounds, promotion
research/goals/*.md               the two research questions and their protocols
research/proposals/*.md           the pending literature proposal
research/leaderboard.md           promoted results, and refusals with the condition that failed
experiments/<id>/experiment.md    eight nodes; lineage in front matter
experiments/<id>/launch.yml       entrypoints, existing format
experiments/<id>/policy.md        present only where it differs from the project default
experiments/template.md           the body template, extended with Expectation and Children
```

`policy.md` is included only for `temporal-adjacency-exclusion`, the one `auto` node. It is a root,
which is what lets it be `auto` at all: a child may narrow its parent's mode, never widen it, so an
`auto` node under a `guided` parent is rejected by `hiraku-auto check`. The guided nodes would
carry a copy of the standard guided policy; it is omitted here to keep the example readable rather
than because it is optional.
