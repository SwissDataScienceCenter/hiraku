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

## Run the example with an agent

`hiraku init` installs the same project-local Hiraku skill for Codex at
`.agents/skills/hiraku/` and for Claude Code at `.claude/skills/hiraku/`. After
completing the setup in section 1, start your coding agent from the initialized
`heart-disease` repository so it can discover the skill, training code, data,
and project state.

Invoke the skill as `$hiraku` in Codex or `/hiraku` in Claude Code. The shared
prompts below use the agent-neutral phrase "Use the Hiraku skill"; use your
agent's explicit invocation syntax when you want to force skill selection.

### Complete workflow from one prompt

The following prompt asks the agent to carry out the same comparison as the
manual workflow:

```text
Use the Hiraku skill to compare logistic regression with a random forest on
the bundled Cleveland heart-disease data. Create a task named heart-disease
that minimizes validation log loss and maximizes validation accuracy. Create
and run a logistic-baseline experiment and a random-forest experiment using
seed 42 and a validation fraction of 0.25, then compare their tracked results
and tell me what the evidence shows.
```

This is a useful manual end-to-end acceptance check for any coding agent that
supports project-local skills. The opt-in automated test described below keeps
its model calls smaller by testing the core actions independently.

### Stepwise prompts

You can instead keep each agent turn focused. These prompts intentionally use
research intent rather than Hiraku CLI syntax:

1. Create the task:

    ```text
    Use the Hiraku skill to create a task named heart-disease. Its goal is to
    compare logistic regression and random forest on the fixed Cleveland
    cohort. Ask whether the nonlinear model improves on the calibrated linear
    baseline. Track validation log loss to minimize and validation accuracy to
    maximize. Only create and verify the task.
    ```

2. Create the baseline experiment:

    ```text
    Use the Hiraku skill to create an experiment named logistic-baseline for
    the active task. Use the summary "Logistic regression baseline" and the
    hypothesis "Regularized logistic regression provides a calibrated
    baseline". It should run train.py with the logistic model, seed 42, and a
    validation fraction of 0.25. Bind it to the current commit. Only create and
    verify the experiment; do not run it.
    ```

3. Run the baseline:

    ```text
    Use the Hiraku skill to run the logistic-baseline experiment and report
    its tracked validation metrics. Do not create or update other records.
    ```

4. Create and run the comparison:

    ```text
    Use the Hiraku skill to create and run a random-forest experiment for the
    active task. Use train.py with the random_forest model, seed 42, and a
    validation fraction of 0.25, bound to the current commit. Report its
    tracked validation metrics.
    ```

5. Compare the evidence:

    ```text
    Use the Hiraku skill to compare the tracked logistic-baseline and
    random-forest runs. Explain which has lower validation log loss and which
    has higher validation accuracy, citing the run IDs and source commits.
    Do not launch another run.
    ```

The first three prompts are covered by the opt-in Codex integration tests. The
remaining prompts provide additional user testing for the full example.

### Verify the agent's work

The finished workflow should contain one task, two experiments, and two
attributed runs:

```bash
hiraku task show heart-disease --json
hiraku experiment list --task heart-disease --json
hiraku run list --task heart-disease \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss --ascending --json
```

Check that both experiments are bound to the initialization commit, use the
same seed and validation split, and differ only in the model argument. The
logistic baseline should have lower validation log loss, while the random
forest should have slightly higher validation accuracy.

Hiraku developers can run the three fresh-repository agent checks explicitly:

```bash
HIRAKU_RUN_CODEX_SKILL_TESTS=1 \
  uv run pytest -n 0 tests/integrations/test_hiraku_skill.py
```

These tests invoke `codex exec` with GPT-5.6 Luna and require local Codex
authentication. They are skipped during the normal test suite and CI.
