# Cleveland Heart Disease example

This example illustrates the Hiraku workflow for developing predictive models on the
[Cleveland cohort heart disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease/).

## 1. Install Hiraku and initialize the example

From a local Hiraku source checkout, install the Hiraku CLI:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install --force .
```

Then create a project and let Hiraku install the training script, processed
dataset, and dependencies for this example.
```bash
hiraku example create heart-disease --git
cd heart-disease
```
Initialization creates `hiraku.yml`, metadata directories, and the agent
skill. The `heart-disease` example also copies `train.py`, `pyproject.toml`, and
`data/processed.cleveland.data` into the project.
Logistic regression and a random forest are included as initial baselines.


If your shell cannot
find `hiraku`, run `uv tool update-shell` and open a new shell.

Install the example's training dependencies and commit the code.
```bash
uv sync
git add .
git add hiraku/dispatch
git commit -m "initialize Heart Disease example"
```



The commit records the exact
training code and dataset that the initial baseline experiments will evaluate.

??? note "Tracking Hiraku metadata in Git"
    In this example, `.gitignore` excludes hiraku metadata (`hiraku.yml`, `hiraku/*`) and the `runs.csv` file.

    Hiraku is (mostly) agnostic to Git state: You can commit metadata, and Hiraku shows what is currently visible in the working tree.
    Merging research records is a user (or agent) responsibility.


## 2. Define the research root

??? tip "Agent workflow using skill"
    Jump to [Run the example with an agent](#using-the-hiraku-skill) to
    see how a coding agent can use the project-local Hiraku skill to perform the
    same workflow.

```bash
hiraku record create heart-disease --type task \
  --set "goal=Develop and evaluate predictive models for heart disease on a fixed Cleveland cohort" \
  --set "question=Which modeling approaches achieve the best validation log loss and accuracy on this cohort?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku record show heart-disease
hiraku status
```

You should see `active_record_id` set to `heart-disease` and the tracking
backend set to CSV.

??? tip "Defining a working branch"
    Store a branch name with `--set 'branch=main'` if the record should point to
    the current branch. Create and switch Git branches explicitly.

## 3. Create a baseline experiment

```bash
hiraku record create logistic-baseline --parent heart-disease --type experiment \
  --set "summary=Logistic regression baseline" \
  --set "hypothesis=Regularized logistic regression provides a calibrated baseline" \
  --commit HEAD

hiraku record show logistic-baseline

# preview and submit the command
hiraku run --record logistic-baseline --backend local --dry-run -- \
  uv run python train.py --model logistic --seed 42 --validation-fraction 0.25

job_id=$(hiraku run --record logistic-baseline --backend local -- \
  uv run python train.py --model logistic --seed 42 --validation-fraction 0.25)
hiraku job wait "$job_id"

# query all runs under the root's tracking source
hiraku runs --record heart-disease --all
# or only the runs attributed to one child record
hiraku runs --record logistic-baseline
```

Expect one run with `val/log_loss` and `val/accuracy`. The default dispatcher
executes a snapshot of the Git-visible source. The
training program writes the CSV row to project-root `runs.csv`.

??? tip "Tracking backends: CSV, Weights & Biases, MLflow and custom backends"
    Hiraku does **not** handle, control or own metric logging. Training scripts log metrics directly to a user-managed tracking backend, independently of Hiraku.


    In this example, results are written to `runs.csv`, but more advanced projects can use [Weights & Biases](../../guides/tracking-wandb.md), [MLflow](../../guides/tracking-mlflow.md), or [custom backends](../../guides/tracking-custom.md).

    Hiraku only **queries the tracking backend for existing runs and metrics**. This means the same training and logging setup can be used with or without Hiraku.


??? note "Dispatchers and isolated execution"
    Hiraku creates an isolated source snapshot and invokes
    the optional `hiraku/dispatch/launch.sh` runtime setup hook.
    `--commit REF` records a base revision; the source digest identifies the
    exact files captured for this execution.

## 4. Run a second experiment

```bash
hiraku record create random-forest --parent heart-disease --type experiment \
  --set "summary=Random forest comparison" \
  --set "hypothesis=Nonlinear feature interactions improve validation log loss" \
  --commit HEAD

job_id=$(hiraku run --record random-forest --backend local -- \
  uv run python train.py --model random_forest --seed 42 \
  --validation-fraction 0.25)
hiraku job wait "$job_id"
hiraku runs --record heart-disease --all --sort val/log_loss
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

## Using the Hiraku skill

`hiraku init` installs the canonical project-local Hiraku skill at
`.agents/skills/hiraku/`, which Codex, Pi, and Gemini CLI discover directly.
Claude Code uses `.claude/skills/hiraku/`, linked to the canonical skill when
directory symlinks are available. After completing the setup in section 1,
start your coding agent from the initialized `heart-disease` repository so it
can discover the skill, training code, data, and project state.

Invoke the skill as `$hiraku` in Codex or `/hiraku` in Claude Code. The shared
prompts below use the agent-neutral phrase "Use the Hiraku skill"; use your
agent's explicit invocation syntax when you want to force skill selection.

### Complete workflow from one prompt

The following prompt asks the agent to establish the same initial baselines as
the manual workflow without limiting the task to those model families:

```text
Use the Hiraku skill to investigate predictive models on the bundled Cleveland
heart-disease data. Create an open-ended root record named heart-disease whose goal is
to identify models with low `val/log_loss` and high `val/accuracy`; do not
restrict the task to particular model families. As initial baselines, create and
run a logistic-baseline experiment and a random-forest experiment using seed 42
and a validation fraction of 0.25, then compare their tracked results and tell
me what the evidence shows.
```

This is a useful manual end-to-end acceptance check for any coding agent that
supports project-local skills. The opt-in automated test described below keeps
its model calls smaller by testing the core actions independently.

### Stepwise prompts

You can instead keep each agent turn focused. These prompts intentionally use
research intent rather than Hiraku CLI syntax:

1. Create the root record:

    ```text
    Use the Hiraku skill to create a root record named heart-disease. Its goal is to
    develop and evaluate predictive models for heart disease on the fixed
    Cleveland cohort without restricting the task to particular model
    families. Ask which modeling approaches achieve the best validation log
    loss and accuracy. Track `val/log_loss` to minimize and `val/accuracy` to
    maximize. Only create and verify the record.
    ```

2. Create the baseline experiment:

    ```text
    Use the Hiraku skill to create a child record named logistic-baseline under
    heart-disease. Use the summary "Logistic regression baseline" and the
    hypothesis "Regularized logistic regression provides a calibrated
    baseline". It should use uv run to invoke train.py with the logistic model,
    seed 42, and a validation fraction of 0.25. Bind it to the current commit.
    Only create and verify the record; do not run it.
    ```

3. Run the baseline:

    ```text
    Use the Hiraku skill to run the logistic-baseline experiment and report
    its tracked validation metrics. Do not create or update other records.
    ```

4. Create and run a second baseline:

    ```text
    Use the Hiraku skill to create and run a random-forest child record as a
    second baseline under heart-disease. Use uv run to invoke train.py with the
    random_forest model, seed 42, and a validation fraction of 0.25, bound to
    the current commit. Report its tracked validation metrics.
    ```

5. Compare the evidence:

    ```text
    Use the Hiraku skill to compare the tracked logistic-baseline and
    random-forest runs. Explain which has lower validation log loss and which
    has higher validation accuracy, citing the run IDs and source commits.
    ```

6. Propose, implement, and run another model:

    ```text
    Use the Hiraku skill to review the heart-disease root record, its baseline results,
    and train.py. Suggest a promising model family that has not yet been
    evaluated and briefly justify it. Create a child record for that model,
    implement the focused support needed in train.py, commit the implementation on the intended working
    branch, bind the record to that commit, and run it with seed 42 and a
    validation fraction of 0.25. Report its tracked metrics compared with the
    two baselines. Do not modify the existing baseline records or rerun the
    baselines.
    ```

### Verify the agent's work

The initial baseline workflow should contain one root, two child records, and
two attributed runs. After the extension prompt, it should contain one
additional child record and attributed run:

```bash
hiraku record show heart-disease
hiraku record tree
hiraku runs --record heart-disease --all \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss --ascending
```

Check that both baselines are bound to the initialization commit, use the same
seed and validation split, and differ only in the model argument. The logistic
baseline should have lower validation log loss, while the random forest should
have slightly higher validation accuracy. The extension experiment should bind
the new implementation commit and use the same evaluation settings.

??? note "Codex integration tests"

    The first three stepwise prompts are covered by the opt-in Codex integration
    tests. The remaining prompts provide additional user testing for the full
    example.

    Hiraku developers can run the three fresh-repository agent checks explicitly:

    ```bash
    HIRAKU_RUN_CODEX_SKILL_TESTS=1 \
    uv run pytest -n 0 tests/integrations/test_hiraku_skill.py
    ```

    These tests invoke `codex exec` with GPT-5.6 Luna and require local Codex
    authentication. They are skipped during the normal test suite and CI.

### Job execution

Jobs capture committed and dirty Git-visible source in an isolated snapshot. Ignored datasets and environments are not copied; configure access through an optional dispatcher. Submission returns a job ID; wait before querying runs. Use `hiraku job logs JOB_ID` for output. Arguments after `--` replace the complete default command.
