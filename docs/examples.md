
# End-to-end example: Cleveland heart-disease classification

The first Hiraku example is a deterministic, end-to-end comparison of logistic
regression and random forest classifiers on the processed Cleveland cohort from
the UCI Heart Disease dataset. It demonstrates the complete core workflow:

1. initialize a Hiraku project in a user-owned Git repository;
2. create one task;
3. create two experiments with experiment-level entrypoints;
4. bind both experiments to one source commit;
5. launch both experiments; and
6. compare their normalized CSV runs.

The example uses only the deterministic Hiraku CLI. It does not use agents,
program execution, templates, dashboards, MLflow, or autonomous loops.

## Example project

The copyable project will live at `examples/heart-disease/` and contain:

```text
examples/heart-disease/
├── data/
│   └── processed.cleveland.data
├── .gitignore
├── README.md
├── pyproject.toml
└── train.py
```

`processed.cleveland.data` is a bundled, checksum-pinned copy of the processed
Cleveland cohort. The example never downloads data, so the workflow and its
automated test can run without network access. `pyproject.toml` declares the
Python, pandas, and scikit-learn requirements. The environment used to run the
example must already have those dependencies and Hiraku installed.

`train.py` owns both model training and the small example-specific CSV writer.
Both models use the same preprocessing, stratified validation split, seed, and
metrics:

- missing numeric values are median-imputed and standardized;
- missing categorical values are most-frequent-imputed and one-hot encoded;
- the validation fraction is `0.25` and the random seed is `42`;
- logistic regression uses `max_iter=2000` and `random_state=42`;
- random forest uses `n_estimators=300`, `random_state=42`, and `n_jobs=1`;
- the reported metrics are `val/log_loss` and `val/accuracy`.

## Initialize the repository

Copy the example outside the Hiraku source checkout. The source commit described
below belongs to this new repository, not to the Hiraku repository.

```bash
cp -R examples/heart-disease ../hiraku-heart-disease
cd ../hiraku-heart-disease
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
```

This commit contains the shared training code, bundled data, dependency
declaration, and files created by `hiraku init`. Both experiments will bind to
this same snapshot.

## Create the task

Create and activate one task. The metric declarations define the default run
columns and their preferred sort directions.

```bash
hiraku task create heart-disease \
  --goal "Compare two model families on a fixed Cleveland cohort" \
  --question "Does a nonlinear random forest improve on a calibrated linear baseline?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'
```

The command creates the Markdown record
`hiraku/tasks/heart-disease/task.md` and makes `heart-disease` the active task.
The record uses YAML front matter for machine-readable fields and an optional
Markdown body.

```bash
hiraku status
hiraku task show
```

## Create the experiments

Create a logistic-regression baseline with its `train` entrypoint. `--commit
HEAD` resolves and stores the full hash of the shared source commit.

```bash
hiraku experiment create logistic-baseline \
  --summary "Logistic regression baseline" \
  --hypothesis "Regularized logistic regression provides a calibrated baseline" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [python, train.py], args: {model: logistic, seed: 42, validation-fraction: 0.25}}}'
```

Create the random-forest comparison in the same task and bind it to the same
commit.

```bash
hiraku experiment create random-forest \
  --summary "Random forest comparison" \
  --hypothesis "Nonlinear feature interactions improve validation log loss" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [python, train.py], args: {model: random_forest, seed: 42, validation-fraction: 0.25}}}'
```

The commands create two Markdown records with YAML front matter:

```text
hiraku/tasks/heart-disease/experiments/logistic-baseline.md
hiraku/tasks/heart-disease/experiments/random-forest.md
```

Each experiment owns its entrypoint declaration. The task has no `train`
entrypoint, and the two experiment namespaces do not merge. The second create
command leaves `random-forest` active, but active experiment selection does not
filter `hiraku run list`.

Inspect the records and their resolved commands with:

```bash
hiraku experiment list
hiraku experiment show logistic-baseline
hiraku experiment entrypoints logistic-baseline
hiraku experiment entrypoints random-forest
```

## Launch the experiments

Launch each entrypoint by qualifying it with its experiment ID:

```bash
hiraku launch logistic-baseline:train
hiraku launch random-forest:train
```

Because each experiment is bound to a commit, Hiraku executes its entrypoint in
an isolated temporary worktree at that commit. The launcher supplies the
training process with these provenance variables:

- `HIRAKU_TASK_ID=heart-disease`;
- `HIRAKU_EXPERIMENT_ID`, matching the selected experiment;
- `HIRAKU_COMMIT`, containing the full shared commit hash;
- `HIRAKU_ENTRYPOINT=train`; and
- `HIRAKU_TASK_FILE`, pointing to the task record in the original project.

The example CSV writer derives `runs.csv` from the parent directory of
`HIRAKU_TASK_FILE`. It therefore appends results to the original project rather
than to the temporary worktree, whose contents are removed after the launch.
Each successful launch appends exactly one row to:

```text
hiraku/tasks/heart-disease/runs.csv
```

The CSV schema is:

```text
id,task_id,experiment_id,commit,timestamp,entrypoint,param.model,param.seed,param.validation_fraction,metric.val/log_loss,metric.val/accuracy
```

The core fields provide run identity and provenance. Parameters use the
`param.` prefix and metrics use the `metric.` prefix required by the CSV
tracking backend. Run IDs and UTC timestamps may vary; attribution,
configuration, and metrics are deterministic.

## Compare the runs

List both normalized runs using the task's default metric declarations:

```bash
hiraku run list
```

Select the example's parameters and metrics explicitly:

```bash
hiraku run list \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy
```

Filter to one experiment:

```bash
hiraku run list \
  --experiment logistic-baseline \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy
```

Rank both experiments by the primary metric:

```bash
hiraku run list \
  --params model,seed,validation_fraction \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss \
  --ascending
```

After the two launches, the project has one task, two experiments, and two
attributed runs. Both runs have the same commit, seed, validation split, and
entrypoint name. They differ only in experiment and model configuration. This
makes the final table a direct, reproducible comparison of the two model
families.

## End-to-end test contract

The corresponding automated test will copy `examples/heart-disease/` to a
temporary directory and execute the workflow above in a newly initialized Git
repository. It must not require network access.

The test will verify that:

- task creation records and activates `heart-disease`;
- both experiment records are valid Markdown records with experiment-level
  `train` entrypoints;
- both experiments contain the same resolved full commit hash;
- both launches succeed and leave exactly two rows in the task-local CSV file;
- each normalized run has the expected task, experiment, entrypoint, model,
  seed, validation fraction, commit, and finite metric values;
- filtering by experiment returns one matching run; and
- sorting by ascending `val/log_loss` produces the expected stable ordering.

The test will not assert generated run IDs or timestamps. With dependencies
pinned by the implemented example, metric values will be asserted exactly or
within documented narrow tolerances.
