# Cleveland Heart Disease example

This offline example compares logistic regression and a random forest on the
processed Cleveland cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease), donated by the dataset's listed creators. The bundled
`data/processed.cleveland.data` has SHA-256 digest
`a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8`.

Install Hiraku and the exact dependencies declared in `pyproject.toml` before
going offline. Then copy this directory outside the Hiraku checkout and run:

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
```

Create the task:

```bash
hiraku task create heart-disease \
  --goal "Compare two model families on a fixed Cleveland cohort" \
  --question "Does a nonlinear random forest improve on a calibrated linear baseline?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku status
hiraku task show
```

Create two experiments bound to the shared source commit:

```bash
hiraku experiment create logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [python, train.py], args: {model: logistic, seed: 42, validation-fraction: 0.25}}}'

hiraku experiment create random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear feature interactions improve validation log loss" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [python, train.py], args: {model: random_forest, seed: 42, validation-fraction: 0.25}}}'

hiraku experiment list
hiraku experiment show logistic-baseline
hiraku experiment entrypoints logistic-baseline
hiraku experiment entrypoints random-forest
```

Launch both models from their commit-isolated worktrees:

```bash
hiraku launch logistic-baseline:train
hiraku launch random-forest:train
```

Compare the task-local CSV runs:

```bash
hiraku run list

hiraku run list \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy

hiraku run list \
  --experiment logistic-baseline \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy

hiraku run list \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss \
  --ascending
```
