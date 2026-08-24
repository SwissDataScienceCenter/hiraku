# NATIX VO and cross-domain alignment — seeded project

The two live research questions, set up as a Hiraku project with the tree structure from
[`docs/autoresearch/`](../../docs/autoresearch/).

## What is real here and what is not

**Real**: the two goals and their questions, the tree structure, the `kind`/`status`/`parent` of every
node, each node's expectation and how it could fail, and the reasoning about what would refute it.

**Not present**: any result. The run table holds a header row and nothing else. Every Findings and
Conclusions section is a marked `**[Gap]**`, and every unknown in the protocols and configuration is a
`**[bracketed question]**`. Nothing is invented — where a number was needed and not known, there is a
gap rather than a plausible-looking value.

**Two nodes are `concluded` with a blank `verdict`** — `occany-baseline` and
`vggt-alignment-baseline`. That is deliberate and the viewer flags it as a problem, correctly: the
work is done but its findings are not recorded here yet. Filling them is step 1.

## The trees

```
vo-backend-selection                ate_rmse_m ↓ · gate completion_rate
└── occany-baseline                 baseline    concluded  [verdict pending]
    ├── vggt-omega                  hypothesis  approved
    ├── vggt-long                   hypothesis  approved
    └── rig-camera-vo               hypothesis  concluded  refutes
        ├── rig-calibration-audit   diagnostic  proposed   (agent-diagnostic)
        └── rig-overlap-geometry    diagnostic  proposed   (agent-diagnostic)

cross-domain-features               align_median_err_m ↓ · gate align_success_rate
└── vggt-alignment-baseline         baseline    concluded  [verdict pending]
    ├── domain-gap-attribution      diagnostic  approved   (agent-diagnostic)
    └── dinov3-features             hypothesis  proposed
```

Three structural choices are arguments, not bookkeeping:

**`rig-camera-vo` is a concluded child with `verdict: refutes`, not a dead end.** "No improvement" is
a result. It has two diagnostic children because two different mechanisms produce that outcome —
calibration or synchronisation error cancelling the added constraint, versus rig geometry that cannot
add constraint in the first place — and they are distinguishable from data already collected, with
different consequences. The first is fixable; the second closes the direction with a reason.

**`domain-gap-attribution` runs before `dinov3-features`.** The plan is to swap features because the
domain gap is suspected. If the binding constraint is something else, a null result from the feature
swap is uninterpretable — no way to separate "DINOv3 does not help" from "the gap was never the
limit". The diagnostic reuses existing outputs and makes the expensive node's result meaningful either
way. This is the investigation test applied to your own plan, not to a failure.

**Both `vggt-omega` and `vggt-long` hang off the baseline as siblings.** They are independent
attempts, so they run in parallel on separate worktrees. `vggt-long` predicts something different from
`vggt-omega` — a flatter drift-versus-length slope rather than a lower level — which is why they are
siblings rather than one node with two settings.

## Getting started

1. **Fill the baselines' Findings** from work already done. The seed-to-seed spread matters most: it
   sets `agreement_tolerance` in `research/config.md` and decides how many seeds every later
   comparison needs. Then set both `verdict` fields.
2. **Answer the bracketed questions** in `research/config.md` and both goal records. The evaluation
   protocol must be fixed before the first run — a protocol settled after the first result is not a
   protocol, and every comparison in the tree depends on it.
3. **Fill in `launch.yml`** for each node with the real entrypoints.
4. **Set the targets** in both goals once the baselines are recorded.
5. Then launch. `vggt-omega` and `vggt-long` are `approved` and independent, so they can go in
   parallel:

```bash
git worktree add .worktrees/vggt-omega exp/vggt-omega
git worktree add .worktrees/vggt-long  exp/vggt-long
```

For `cross-domain-features`, `domain-gap-attribution` is the cheap first move — it reuses the
baseline's outputs and decides whether `dinov3-features` is the right intervention at all.

## Finding new methods to try

`research/proposals/` is empty. The first question asks for help exploring which methods are worth
trying beyond OccAny, VGGT-Omega and VGGT-Long; that is the discovery workflow in
[`docs/autoresearch/agent-workflows/discover-hypotheses.md`](../../docs/autoresearch/agent-workflows/discover-hypotheses.md),
and it writes proposals here for a decision. It is deliberately not seeded with guesses — a proposal
has to argue from sources why a method transfers to this data, and answer the investigation test,
before it is worth your attention.

Run it only after step 2. A proposal cannot argue novelty against the tree, or fit within budget, if
the protocol and bounds are not yet written down.

## View it

```bash
hiraku-auto --project examples/natix-vo dash -o dashboard.html
hiraku-auto --project examples/natix-vo ui
```
