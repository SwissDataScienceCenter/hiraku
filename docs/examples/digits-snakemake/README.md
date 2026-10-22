# Digits classification with Snakemake and MLflow

Run scikit-learn's bundled Digits dataset (1,797 8×8 images) through a Snakemake
workflow and log every epoch to MLflow. The workflow compares weak and strong
L2 regularization with the same stratified split, seed, hidden layer, and
40-epoch budget as the [Digits MLflow example](../digits-mlflow/README.md).

Each step is an entrypoint of the record it belongs to:

| Rule | Record · entrypoint | Code | Reads | Writes |
| --- | --- | --- | --- | --- |
| `digits__prepare` | `digits` · `prepare` | `scripts/prepare_data.py` | the script | `data/digits.npz` |
| `weak_l2__train` | `weak-l2` · `train` | `scripts/train.py --alpha 0.0001` | `data/digits.npz` | `results/weak-l2.json`, an MLflow run |
| `strong_l2__train` | `strong-l2` · `train` | `scripts/train.py --alpha 1.0` | `data/digits.npz` | `results/strong-l2.json`, an MLflow run |

The records state the code and its arguments. The bundled `Snakefile` states
the files and runs each step with `hiraku launch`. The `digits` record keeps
one CSV run per workflow execution, and the experiment records keep their
MLflow runs. The [Snakemake pipelines](../snakemake-pipelines/index.md) pages
apply the same approach to every other example.

## Setup

Install Hiraku with its MLflow, Snakemake, and dashboard extras first, from the
Hiraku checkout:

```bash
uv tool install --force --python 3.12 '.[mlflow,snakemake,dashboard]'
```

Snakemake depends on `immutables`, which may fail to build on Python versions
without a prebuilt wheel. Then create and initialize a new project:

```bash
mkdir digits-snakemake
cd digits-snakemake
git init
hiraku init --example digits-snakemake
uv sync
git add .
git commit -m "initialize Digits Snakemake example"
```

`hiraku init` copies the scripts, the `Snakefile`, and `pyproject.toml`.
`uv sync` creates `.venv` with the training libraries and Snakemake. No
tracking server or credentials are required: MLflow stores runs in the local
`mlflow.db`.

## Create the records

```bash
hiraku record create digits --type task \
  --set "goal=Compare regularization strengths in a Snakemake workflow" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]' \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}' \
  --set 'entrypoints={prepare: {command: [python, scripts/prepare_data.py]}}'

hiraku record create weak-l2 --parent digits --type experiment \
  --set "summary=Weak L2 baseline" \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}' \
  --set 'entrypoints={train: {command: [python, scripts/train.py], args: {alpha: 0.0001, epochs: 40, hidden-units: 64, seed: 42}}}'

hiraku record create strong-l2 --parent digits --type experiment \
  --set "summary=Stronger L2 comparison" \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}' \
  --set 'entrypoints={train: {command: [python, scripts/train.py], args: {alpha: 1.0, epochs: 40, hidden-units: 64, seed: 42}}}'

git add .
git commit -m "add Digits records"
```

`digits` is the workflow record: its CSV backend in `hiraku/runs.csv` will hold
one run per workflow execution, and its `prepare` entrypoint writes the data.
The two experiments use MLflow and each have a `train` entrypoint. The
commands run `python`, so they need the project's `.venv`: Snakemake runs
inside it below, and `uv run hiraku launch` runs a single step inside it, for
example `uv run hiraku launch train --record weak-l2 -- --input
data/digits.npz --output results/weak-l2.json` once the data exists.
`hiraku record show weak-l2` shows the exact command of that step.

## Run the workflow

```bash
hiraku snakemake run --record digits --config 'train=--epochs 2' \
  --command uv --command-arg run --command-arg snakemake
```

What happens:

1. Hiraku writes a `started` run of `digits` to `hiraku/runs.csv`.
2. `uv run snakemake` reads the bundled `Snakefile` inside the project
   environment. `rule all` asks for both `results/*.json`, so Snakemake runs
   `digits__prepare` first and then both training rules.
3. Each rule runs `hiraku launch ENTRYPOINT --record RECORD`, so the command and
   its arguments come from the record. `--config 'train=--epochs 2'` adds
   `--epochs 2` to both training commands, for a quick run.
4. Each training step logs an MLflow run tagged with the workflow run's ID and
   writes its result JSON.
5. Hiraku replaces the `started` run with the final one: `outcome: succeeded`,
   and one entry per step in `jobs`.

Run the same command again:

```bash
hiraku snakemake run --record digits --config 'train=--epochs 2' \
  --command uv --command-arg run --command-arg snakemake
```

Every file is up to date, so nothing runs. The execution is stored as
`no-op`, with every step marked as not run. While `hiraku/` is tracked in the
code repository, the new rows make the next run's `git_state` `dirty`. Ignore
`hiraku/` or keep it in a separate repository to keep the code checkout clean
between executions.

## Inspect the results

```bash
hiraku runs --record digits --json
hiraku runs --record weak-l2 --json
hiraku dashboard --record digits
```

The workflow run's `raw.jobs` lists each step with its record, entrypoint,
command, files, and whether it ran. Each MLflow run has `entrypoint: train`,
the executed commit, and the regularization parameters; its
`raw.tags["hiraku.workflow_run_id"]` equals the workflow run's ID, which
`results/<variant>.json` repeats next to its `mlflow_run_id`. In the dashboard,
selecting the workflow run lists its steps, and **Files** shows which step
produces each file and which steps use it.

To follow the **Open in mlflow** links, add `ui_url: "http://127.0.0.1:5000"`
to the experiment records' `tracking_backend` and serve the UI next to the
dashboard:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000
```

`hiraku dashboard --init --edit --record digits` copies the dashboard into the
project and opens it in marimo-studio, where `files_df` holds the file table.

## Without the Snakefile

The bundled `Snakefile` is optional. Remove it and launch each step once with
`--observe` instead:

```bash
git rm -q Snakefile
git commit -m "build the workflow from observed launches"
rm -rf data results
uv run hiraku launch prepare --record digits --observe -- --output data/digits.npz
uv run hiraku launch train --record weak-l2 --observe -- \
  --input data/digits.npz --output results/weak-l2.json --epochs 2
uv run hiraku launch train --record strong-l2 --observe -- \
  --input data/digits.npz --output results/strong-l2.json --epochs 2
hiraku snakemake run --record digits \
  --command uv --command-arg run --command-arg snakemake
```

What happens:

1. Each observed launch runs its step and notes, in
   `hiraku/snakemake/observed/`, the files it read and wrote: `prepare` wrote
   `data/digits.npz`, and each `train` read it and wrote its result. Git
   ignores `hiraku/snakemake/`.
2. Without a `Snakefile`, `hiraku snakemake run` writes
   `hiraku/snakemake/Snakefile` from the observations: the same three rules as
   the bundled file, each repeating its observed arguments. `mlflow.db` is left
   out, because both training steps write it.
3. Snakemake remembers, in `.snakemake/`, the rule that last built each file.
   The earlier runs built these files with the bundled rules, and the rules
   have changed, so it runs all three steps again (`succeeded`). In a project
   that never ran the bundled `Snakefile`, the launches have already built
   every file and the first execution is a `no-op`.

Which to keep? Observation is the shortest path when you run the steps
yourself. The bundled `Snakefile` is the form an agent can write and maintain:
its rules only wire files to `hiraku launch` of the records' entrypoints, so an
edit cannot change the code a record runs. `--dry-run` checks them before any
training, each run's `jobs` show what ran, and a reviewer reads the change as a
diff. Restore it with `git revert HEAD`, or write the observed rules to a new
`Snakefile` with `hiraku snakemake generate --record digits --observed`.

See [Snakemake tracking](../../guides/tracking-snakemake.md) for the stored
columns and the execution limits.
