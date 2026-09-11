# Cleveland Heart Disease example

This bundled example compares logistic regression and a random forest on the
processed Cleveland cohort from the
[UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease/).

## 1. Install Hiraku and initialize the example

From a local Hiraku source checkout, install the CLI:

```bash
uv tool install .
```

Then create a project and let Hiraku install the training script, processed
dataset, and dependencies for this example:

```bash
mkdir heart-disease
cd heart-disease
git init
hiraku init --example heart-disease
uv sync
git add .
git commit -m "initialize Heart Disease example"
```

`uv tool install .` installs the checked-out Hiraku CLI on your PATH.
`uv sync` installs the example's training dependencies. If your shell cannot
find `hiraku`, run `uv tool update-shell` and open a new shell.

Initialization creates `hiraku.yml`, metadata directories, and the agent
skill. The `heart-disease` example also copies `train.py`, `pyproject.toml`, and
`data/processed.cleveland.data` into the project. The commit records the exact
training code and dataset that both experiments will evaluate.

??? note "Tracking Hiraku metadata in git"
    In this example, `.gitignore` excludes hiraku metadata (`hiraku.yml`, `hiraku/*`) and the `runs.csv` file.

    Hiraku is (mostly) agnostic to Git state: You can commit metadata, and Hiraku shows what is currently visible in the working tree.
    Merging task and experiment records is a user (or agent) responsibility.

## 2. Define the task

```bash
hiraku task create heart-disease \
  --goal "Compare two model families on a fixed Cleveland cohort" \
  --question "Does a nonlinear random forest improve on a calibrated linear baseline?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku status

hiraku task show heart-disease
# omit task name since it's the current active task
hiraku task show  
```

You should see `active_task_id` to be `heart-disease` and the tracking backend to be
CSV. 

Hiraku records a suggested task branch in the task metadata. Pass
`--branch main` to `hiraku task create` if you want the task record to point to
the current branch instead.

## 3. Run the baseline and inspect its result

```bash
hiraku experiment create logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, validation-fraction: 0.25}}}'

# show entrypoint definitions
hiraku experiment entrypoints

# preview the command
hiraku launch logistic-baseline:train --dry-run

hiraku launch logistic-baseline:train

# query runs
hiraku run list --task heart-disease
# omitted task defaults to active task
hiraku run list
```

Expect one run with `val/log_loss` and `val/accuracy`. The default dispatcher
runs the current working state and reports its commit and clean/dirty state. The
training program writes the CSV row to project-root `runs.csv`.

??? note "Launch dispatchers and isolated execution"
    By default `hiraku launch logistic-baseline:train` passes the entrypoint command to a bash script in `hiraku/dispatch/launch.sh`, which executes the command. Launch scripts are project data that can be modified to support custom launch dispatch behaviour, e.g. submitting a job to a compute platform.

    Hiraku ships a second default `worktree` dispatcher, which runs the entrypoint in a separate worktree at the commit specified by the experiment. Try the following:
    ```bash
    hiraku launch --dispatch worktree logistic-baseline:train
    hiraku launch --dispatch worktree --commit <sha> logistic-baseline:train
    ```


## 4. Compare another model

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
    The table below shows results produced by the commands above. Click a column
    heading to sort it.

    | Experiment | Run | Model | val/log_loss | val/accuracy |
    | --- | --- | --- | ---: | ---: |
    | `logistic-baseline` | `23b1c42c` | Logistic regression | 0.32254890 | 0.85526316 |
    | `random-forest` | `880d2cd3` | Random forest | 0.35226763 | 0.86842105 |
