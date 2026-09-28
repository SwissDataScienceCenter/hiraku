# Heart disease classification with CSV

This example compares regularized logistic regression and a random forest on
the processed Cleveland heart disease dataset. It writes validation log loss
and accuracy to a project-root CSV file with Hiraku record attribution.

```bash
mkdir heart-disease && cd heart-disease
git init -b main
hiraku init --example heart-disease
uv sync
git add . && git commit -m "initialize Heart Disease example"
```

Create a root record for the research goal and a child record for each model.
Both children bind the source commit used for their implementation. Metrics
defined on the root are inherited by both children.

```bash
hiraku record create heart-disease --type task \
  --set 'goal=Minimize validation log loss on the Cleveland cohort' \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku record create logistic-baseline --parent heart-disease \
  --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, validation-fraction: 0.25}}}'

hiraku record create random-forest --parent heart-disease \
  --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: random_forest, seed: 42, validation-fraction: 0.25}}}'

hiraku record tree
hiraku record show logistic-baseline --json
hiraku launch train --record logistic-baseline
hiraku launch train --record random-forest
hiraku runs --record heart-disease --descendants --sort val/log_loss --json
```

The `train.py` script records the run ID, `record_id`, commit, Git state,
parameters, and metrics in `runs.csv`. A direct query on a model record returns
its runs; `--descendants` on the root combines both models. Use the recorded
metrics and notes to document a decision on each child record.
