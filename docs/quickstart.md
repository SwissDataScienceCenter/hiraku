# Hiraku quickstart

This is the shortest linear path from a Git repository to one reviewed experiment. See the
[tutorial](tutorial.md) for explanations and [advanced workflows](advanced.md) for experiment
branches, reviews, and remote MLflow.

## 1. Initialize and define the task

Install Hiraku, enter an existing Git repository, and initialize it:

```bash
uv tool install hiraku
hiraku init
git add .hiraku.yml .hiraku .agents
git commit -m "initialize Hiraku"
hiraku task create heart-disease --template guided
git switch hiraku/task/heart-disease
```

Edit `tasks/heart-disease/task.md` so its goal is strategy-neutral and its fixed protocol declares
`val/log_loss` to minimize and `val/accuracy` to maximize. Define distinct `logistic` and
`random-forest` entrypoints in `launch.yml`, then approve the guided checkpoint and commit the task.

## 2. Create, implement, and bind an experiment

```bash
hiraku exp create heart-disease:logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --entrypoint logistic
```

Implement the approved baseline, review it, and commit it. Then bind the exact snapshot:

```bash
git add .
git commit -m "implement logistic baseline"
hiraku exp set heart-disease:logistic-baseline --commit HEAD
```

## 3. Launch, inspect, and conclude

After the guided launch checkpoint is approved:

```bash
hiraku launch --experiment logistic-baseline heart-disease:logistic
hiraku runs heart-disease --sort val/log_loss --ascending
hiraku runs heart-disease --json
```

Use the JSON evidence and declared metric directions to update the experiment's status, decision,
and conclusion. Finally synthesize reviewed findings and next steps in `task.md` and append its
`## Changelog` entry. Hiraku never edits backend-managed run data for you.
