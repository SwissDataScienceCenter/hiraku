# Iris with Weights & Biases

This example trains classifiers on scikit-learn's Iris dataset and logs runs
to W&B. Install the SDK, authenticate, and initialize this directory:

```bash
uv sync
uv run wandb login
git init -b main
hiraku init
git add . && git commit -m "initialize Iris example"
```

Set your W&B entity and project as needed. Create a root record with shared
tracking settings and a child for each model:

```bash
hiraku record create iris --type task \
  --set 'goal=Maximize Iris test accuracy' \
  --set 'metrics=[{name: test/accuracy, direction: maximize}]' \
  --set 'tracking_backend={type: wandb, project: hiraku-iris}'

hiraku record create logistic --parent iris --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, test-size: 0.25}}}'

hiraku record create random-forest --parent iris --type experiment --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: random_forest, seed: 42, test-size: 0.25}}}'

hiraku launch train --record logistic
hiraku launch train --record random-forest
hiraku runs --record iris --descendants --json
```

The training script writes `hiraku.record_id`, commit, and entrypoint into the
run config. Hiraku queries W&B after runs sync; W&B retains the full remote
history. The script owns logging and project creation.
