# Cleveland Heart Disease example

This bundled example compares logistic regression and a random forest on the
processed Cleveland cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease)/

## 1. Install Hiraku and copy the example

Start from a directory where you keep projects. If you already cloned Hiraku,
start with `cd hiraku`.

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install .
cp -R examples/heart-disease ../hiraku-heart-disease
cd ../hiraku-heart-disease
uv sync
```

`uv tool install .` installs the checked-out Hiraku CLI on your PATH.
`uv sync` installs the example's training dependencies. If your shell cannot
find `hiraku`, run `uv tool update-shell` and open a new shell.

## 2. Initialize and save the source

```bash
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
```

Initialization creates `hiraku.yml`, metadata directories, and the agent
skill. The commit records the training code and bundled dataset that both
experiments will evaluate.

## 3. Define the task

```bash
hiraku task create heart-disease \
  --goal "Compare two model families on a fixed Cleveland cohort" \
  --question "Does a nonlinear random forest improve on a calibrated linear baseline?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku status

hiraku task show heart-disease
hiraku task show  # omit task name since it's the current active task

```

You should see `active_task_id` to be `heart-disease` and the tracking backend to be
CSV. 

Note that hiraku by default assigns the branch `/hiraku/tasks/heart-disease` to the task. You can explicitly assign `--branch main`. 

## 4. Run the baseline and inspect its result

```bash
hiraku experiment create logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, validation-fraction: 0.25}}}'

hiraku launch logistic-baseline:train

# query runs
hiraku run list --task heart-disease
# omitted task defaults to active task
hiraku run list  
```

Expect one run with `val/log_loss` and `val/accuracy`. Hiraku executes the
bound commit in a temporary worktree. The training program writes the CSV row
back to the original task directory.

## 5. Compare another model

```bash
hiraku experiment create random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear feature interactions improve validation log loss" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: random_forest, seed: 42, validation-fraction: 0.25}}}'

hiraku launch random-forest:train
hiraku run list
```

Expect two runs, with lower log loss first. Both use the same committed
script, cohort, split, and seed; the model argument changes. Exact metric
values depend on the dependency versions.

Each listing reflects records written by the training program. A failed
launch that never logs a row will not appear as a run.

??? info "Example run results"
    The table below is illustrative documentation data. Click a column heading
    to sort it; real values are produced by the commands above.

    | Experiment | Run | Model | val/log_loss | val/accuracy | Status |
    | --- | --- | --- | ---: | ---: | --- |
    | `logistic-baseline` | `run-001` | Logistic regression | 0.3523 | 0.8421 | completed |
    | `random-forest` | `run-002` | Random forest | 0.3187 | 0.8684 | completed |
    | `random-forest` | `run-003` | Random forest | 0.3211 | 0.8553 | completed |
