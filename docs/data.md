# Record data format

Each root record owns one directory. All descendants of that root are flat
files in the same directory:

```text
hiraku/records/heart-disease/heart-disease.md
hiraku/records/heart-disease/logistic-baseline.md
hiraku/records/heart-disease/calibration-diagnosis.md
```

The `parent_id` field, rather than the file path, defines the logical tree.
Use `hiraku record update ID --parent NEW_PARENT` or `Record.reparent()` to
move a record and its descendants across roots; `--root` makes it a root.
Use `hiraku record tree` to inspect nesting. Directly editing `parent_id`
across roots also requires moving the files to the matching directory.

```markdown
---
id: logistic-baseline
parent_id: heart-disease
type: experiment
related_to: [prior-feature-selection]
commit: 0123456789abcdef0123456789abcdef01234567
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args:
      model: logistic
status: implemented
---
# Logistic baseline

Evaluate validation log loss against the shared task metrics.
```

Any other YAML field may document the research. Omitted fields inherit through
`parent_id`; an explicit `null` blocks inheritance. Entrypoints merge by name.
`id`, `parent_id`, `related_to`, `type`, `commit`, `status`, and `decision` do not
inherit. `related_to` has no effect on the tree.

The generic `BaseRecord` class provides Markdown persistence to research
records, agent programs, actions, and contexts. Only research `Record` objects
use the `hiraku/records/` registry and inheritance rules.

## Root records and child records

A root can collect the goal, question, evaluation protocol, shared metrics,
tracking backend, and reusable entrypoints for an effort. Its Markdown body
remains the human-readable place for the implementation plan, results, open
questions, reviewed findings, and changelog. For example:

```markdown
---
id: heart-disease
type: task
goal: Minimize validation log loss on the fixed Cleveland cohort.
question: Which model family performs best under the shared protocol?
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
tracking_backend:
  type: csv
entrypoints:
  evaluate:
    command: [uv, run, python, evaluate.py]
---
# Heart disease risk prediction

## Evaluation protocol

## Results

## Reviewed decisions
```

A child can start as a proposal with a hypothesis and no `commit`. Bind its
implementation with `hiraku record update logistic-baseline --commit HEAD`
after the code is committed. The stored `commit` must be a full lowercase Git
hash. A child may define an entrypoint for its own investigation, override a
shared one, or inherit one unchanged. Records stay editable throughout an
investigation; a `decision` and narrative conclusion can be added after runs
are reviewed.

```markdown
---
id: logistic-baseline
parent_id: heart-disease
type: experiment
summary: Logistic regression baseline
hypothesis: Regularized regression provides a calibrated baseline.
status: proposed
decision: pending
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args:
      model: logistic
      seed: 42
---
## Proposal

Evaluate this model under the shared split and metrics.

## Results and conclusion
```

```bash
hiraku record create heart-disease --type task \
  --set 'goal=Improve heart disease prediction'
hiraku record create logistic-baseline --parent heart-disease \
  --type experiment \
  --set 'hypothesis=Regularized regression is a useful baseline'
hiraku record update logistic-baseline --commit HEAD
hiraku record show logistic-baseline
hiraku record entrypoints logistic-baseline
hiraku record tree
```

Creation sets the active record unless `--no-activate` is supplied. The
selection is stored as `active_record_id` in `hiraku.yml`; an explicit
`--record` takes precedence for commands that accept it. Listing and tree
commands use records visible in the current checkout.

## Execution and run attribution

Entrypoints declare repeatable commands and default arguments. The local
dispatcher executes the current checkout and records whether it is clean or
dirty; `--dispatch worktree` executes an exact commit in a separate checkout.
Custom project dispatchers can integrate with a compute platform. The launch
context includes `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT`, and
`HIRAKU_ENTRYPOINT`. A dispatcher supplies `HIRAKU_COMMIT` and
`HIRAKU_GIT_STATE` to the training process.

One run belongs to one record. For example, a CSV logger can write:

```csv
id,record_id,commit,git_state,timestamp,entrypoint,metric.val/log_loss,param.seed
run-001,logistic-baseline,0123456789abcdef0123456789abcdef01234567,clean,2026-01-01T12:00:00Z,train,0.32,42
```

`hiraku runs --record logistic-baseline` queries that record's runs.
`hiraku runs --record heart-disease --descendants` includes runs from all
children. The backend and training script own logging, while Hiraku provides
selection, normalization, and comparison.
