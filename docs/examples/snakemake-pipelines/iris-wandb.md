# Iris with W&B as a Snakemake pipeline

This page trains both Iris classifiers of the
[Iris W&B example](../iris-wandb/README.md) in one workflow. It runs W&B
offline, so it needs no account.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `logistic__train` | `logistic` · `train` | `train.py` | a W&B run |
| `random_forest__train` | `random-forest` · `train` | `train.py` | a W&B run |

## 1. Create the project with its records

```bash
mkdir iris-wandb-pipeline
cd iris-wandb-pipeline
git init
hiraku init --example iris-wandb
uv sync
git add .
git commit -m "initialize Iris W&B example"
export WANDB_MODE=offline
hiraku record create iris --type task \
  --set "goal=Maximize Iris test accuracy" \
  --set 'metrics=[{name: test/accuracy, direction: maximize}]' \
  --set 'tracking_backend={type: wandb, project: hiraku-iris}'
hiraku record create logistic --parent iris --type experiment \
  --set "summary=Logistic regression baseline" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, test-size: 0.25}}}'
hiraku record create random-forest --parent iris --type experiment \
  --set "summary=Random forest comparison" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: random_forest, seed: 42, test-size: 0.25}}}'
```

These are the example's records, shortened. `WANDB_MODE=offline` makes
`train.py` write its W&B runs to the local `wandb/` directory instead of
uploading them; run `uv run wandb login` and leave it unset to sync with W&B.
Hiraku reads runs back from the W&B server, so offline runs do not appear in
`hiraku runs` or the dashboard.

## 2. Add the workflow record

```bash
hiraku record create pipeline --parent iris --type task \
  --set "summary=Train both Iris classifiers" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/logs/' 'wandb/' >> .gitignore
git add .
git commit -m "add the pipeline record"
```

The workflow runs are CSV rows on `pipeline`, so they are visible even while
the training runs stay offline.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
hiraku launch train --record logistic --observe
hiraku launch train --record random-forest --observe
cat hiraku/snakemake/observed/logistic__train.json
```

The only input is `train.py`: the Iris data comes with scikit-learn, outside
the project. `wandb/` is never observed, so the steps have no outputs.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope iris
cat hiraku/snakemake/Snakefile
```

Each generated rule keeps its step's printed output as
`hiraku/snakemake/logs/<rule>.log`. Both classifiers train again, and the
workflow run is `succeeded`.

### 5. Run it again

```bash
hiraku snakemake run --record pipeline --scope iris
```

Nothing changed: `no-op`.

## Path B: write the Snakefile

### 6. Write the rules

```bash
cat > Snakefile <<'EOF'
# Train both Iris classifiers.

rule all:
    input: "logs/logistic.log", "logs/random-forest.log"

rule logistic__train:
    input: "train.py"
    output: "logs/logistic.log"
    shell: "hiraku launch train --record logistic > {output}"

rule random_forest__train:
    input: "train.py"
    output: "logs/random-forest.log"
    shell: "hiraku launch train --record random-forest > {output}"
EOF
git add Snakefile
git commit -m "add the Iris Snakefile"
```

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline
hiraku snakemake run --record pipeline
```

`planned`, then `succeeded`, then `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku dashboard --record iris
```

The dashboard shows the `pipeline` workflow runs and their steps. With W&B
offline, the experiments show no training runs; after `uv run wandb login`
and an online run, they appear with links to W&B.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Each step's file | `hiraku/snakemake/logs/<rule>.log`, also printed | `logs/<record>.log` |
