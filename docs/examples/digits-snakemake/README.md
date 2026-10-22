# Digits classification with Snakemake and MLflow

Run scikit-learn's bundled Digits dataset (1,797 8×8 images) through a Snakemake
DAG and log every epoch to MLflow. The workflow compares weak and strong L2
regularization with the same stratified split, seed, hidden layer, and 40-epoch
budget as the [Digits MLflow example](../digits-mlflow/README.md).

Each step is an entrypoint of the record it belongs to:

| Rule | Record · entrypoint | Code | Reads | Writes |
| --- | --- | --- | --- | --- |
| `digits__prepare` | `digits` · `prepare` | `scripts/prepare_data.py` | the script | `data/digits.npz` |
| `weak_l2__train` | `weak-l2` · `train` | `scripts/train.py --alpha 0.0001` | `data/digits.npz` | `results/weak-l2.json`, an MLflow run |
| `strong_l2__train` | `strong-l2` · `train` | `scripts/train.py --alpha 1.0` | `data/digits.npz` | `results/strong-l2.json`, an MLflow run |

The records state the code and its arguments; the `Snakefile` states the files
and runs each step with `hiraku launch`. The `digits` record keeps one CSV run
per workflow execution, and the experiment records keep their MLflow runs.

## Setup

Install Hiraku with its MLflow, Snakemake, and dashboard extras in your CLI
environment first (from the Hiraku checkout: `uv tool install --force --python
3.12 '.[mlflow,snakemake,dashboard]'`). Snakemake depends on `immutables`, which
may fail to build on Python versions without a prebuilt wheel.
Then create and initialize a new project:

```bash
mkdir digits-snakemake
cd digits-snakemake
git init
hiraku init --example digits-snakemake
uv sync
git add .
git commit -m "initialize Digits Snakemake example"
```

`uv sync` installs the example's training and Snakemake dependencies. No
tracking server or credentials are required; MLflow stores runs in the local
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

`hiraku record show weak-l2` now shows the exact command of its step, and
`hiraku launch train --record weak-l2 -- --input data/digits.npz --output
results/weak-l2.json` runs that step alone.

## Run the workflow

```bash
hiraku snakemake run --record digits \
  --command uv --command-arg run --command-arg snakemake
```

`--command uv --command-arg run --command-arg snakemake` runs Snakemake in the
project environment, so the launched `python` is the one `uv sync` installed.
Add `--config 'train=--epochs 2'` for a quick run; the extra arguments reach
both training steps and appear in their recorded commands.

The evidence is written to `hiraku/runs.csv`, next to the records, so it is
versioned with them. Run the same command again:

```bash
hiraku snakemake run --record digits \
  --command uv --command-arg run --command-arg snakemake
```

The second execution is stored as `no-op`, with every step marked as not run.
While `hiraku/` is tracked in the code repository, the new rows make its
`git_state` `dirty`. Ignore `hiraku/` or keep it in a separate repository to
keep the code checkout clean between executions.

## Without the Snakefile

The bundled `Snakefile` is optional. Remove it and launch each step once
instead; each launch notes the files it read and wrote:

```bash
git rm -q Snakefile
git commit -m "build the workflow from observed launches"
hiraku launch prepare --record digits -- --output data/digits.npz
hiraku launch train --record weak-l2 -- \
  --input data/digits.npz --output results/weak-l2.json
hiraku launch train --record strong-l2 -- \
  --input data/digits.npz --output results/strong-l2.json
hiraku snakemake run --record digits \
  --command uv --command-arg run --command-arg snakemake
```

`hiraku snakemake run` writes `hiraku/snakemake/Snakefile` with the same three
rules as the bundled file, and leaves out `mlflow.db`, which both training
steps write. The launches already built every file, so the first execution is
a `no-op`; delete `data/digits.npz` and run again to rebuild all three steps.

## Inspect the results

```bash
hiraku runs --record digits --json
hiraku runs --record weak-l2 --json
hiraku dashboard --record digits
```

The workflow run's `raw.jobs` lists each step with its record, entrypoint,
command, files, and whether it ran. Each MLflow run has `entrypoint: train`,
the executed commit, and the regularization parameters; its
`raw.tags["hiraku.workflow_run_id"]` equals the workflow run ID, which
`results/<variant>.json` repeats next to its `mlflow_run_id`. In the dashboard,
selecting the workflow run lists its steps, and the Files view shows which step
produces each file and which steps use it.

To follow the **Open in mlflow** links, add `ui_url: "http://127.0.0.1:5000"`
to the experiment records' `tracking_backend` and serve the UI next to the
dashboard:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000
```

`hiraku dashboard --init --edit --record digits` copies the dashboard into the
project and opens it in marimo-studio, where `files_df` holds the file table.

See [Snakemake tracking](../../guides/tracking-snakemake.md) for the stored
columns and the execution limits.
