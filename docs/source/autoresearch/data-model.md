# Data model

## Goal

A Goal is the unit of comparability. It records the question and the protocol that makes its Runs
meaningful: primary metric, `min`/`max` direction, optional target, evaluation protocol, constraints,
and an immutable source baseline commit. Goal configuration lives in SQLite, not on Experiment
branches.

## Experiment

An Experiment is one implementation attempt:

```text
id, goal_id, parent_id, kind, hypothesis, branch,
parent_commit, evaluated_commit, status, verdict, origin, timestamps
```

`kind` is `baseline` or `hypothesis`; `origin` is `guided` or `auto`. `parent_id` is research
lineage, not inferred from Git. Intermediate Git commits remain implementation history. On first
launch `evaluated_commit` is frozen; subsequent source changes require a new sibling or child.

This preserves the useful autoresearch rule: a diagnostic or refinement is still an Experiment, so the
tree can be arbitrarily deep without inventing a second research object.

## Run

A Run is one execution of one exact Experiment commit. SQLite stores lifecycle fields, exit code,
entrypoint, timestamps, parameters, metrics, and JSON provenance. A Run is created while `running`
before the child starts and finalized as `completed`, `failed`, or `killed`.

`run_parameters` is append/immutable per key. `run_metrics` is updatable while the Run is active.
Flattened CLI rows expose parameters as `param/<key>` and metric names as columns.

## Proposal and events

A Proposal is an auto-mode suggestion with a prospective Experiment ID and hypothesis. Admission turns
it into an ordinary Experiment; it is never a second Experiment type. The append-only `events` table
records lifecycle transitions and failure classification for auditability.

## SQLite and projections

`.hiraku/state.db` uses schema versioning, foreign keys, WAL mode, and transactional writes. It is
ignored by Git. `.hiraku.yml` remains tracked project configuration. Goal launch manifests are
validated at Goal creation and persisted in the Goal row, avoiding metadata disappearing when a
branch is created from an older source commit.

Generated `.hiraku/reports/*.md`, `*.json`, and `*-runs.csv`, plus `hiraku tui`, are projections. They
must be regenerated from SQLite and never become competing lifecycle sources of truth.

## Persistence boundary

Application services access state through the repositories in
`hiraku.infrastructure.sqlite`. Repositories perform SQL row access; services retain validation,
domain rules, and transaction boundaries. This makes it possible to test lifecycle behavior with a
fake `StateStore` and to test the SQLite implementation independently.
