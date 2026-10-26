# Digits with MLflow as a Snakemake pipeline

This page trains both regularization variants of the
[Digits MLflow example](../digits-mlflow/README.md) in one workflow, while
each training run keeps its learning curves in MLflow.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `weak_l2__train` | `weak-l2` · `train` | `train.py` | an MLflow run in `mlflow.db` |
| `strong_l2__train` | `strong-l2` · `train` | `train.py` | an MLflow run in `mlflow.db` |

## 1. Create the project

```bash
mkdir digits-mlflow-pipeline
cd digits-mlflow-pipeline
git init
hiraku init --example digits-mlflow --with-results
uv sync --python 3.12
git add .
git commit -m "initialize Digits MLflow example with results"
```

`--with-results` copies the example's records and a prepared `mlflow.db`. The
task `digits` uses the MLflow backend, and its experiments `weak-l2` and
`strong-l2` each have a `train` entrypoint that trains for 40 epochs with
their own L2 strength. The database works without an MLflow server.

## 2. Add the workflow record

```bash
hiraku record create pipeline --parent digits --type task \
  --set "summary=Train both regularization variants" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/logs/' >> .gitignore
git add .
git commit -m "add the pipeline record"
```

`pipeline` needs a CSV backend for its workflow runs, while `digits` and its
experiments keep MLflow. Git ignores Snakemake's `.snakemake/` state and the
`logs/` of Path B.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
hiraku launch train --record weak-l2 --observe -- --epochs 2
hiraku launch train --record strong-l2 --observe -- --epochs 2
cat hiraku/snakemake/observed/weak-l2__train.json
```

Each launch trains for two epochs instead of 40, for a quick run, and logs an
MLflow run. Hiraku records the call-time arguments with the files:

```json
{
  "record": "weak-l2",
  "entrypoint": "train",
  "options": [],
  "arguments": ["--epochs", "2"],
  "inputs": ["train.py"],
  "outputs": [],
  "observed_at": "..."
}
```

Both steps wrote `mlflow.db`. It is not an output: it is the records' tracking
database, and a file several steps write cannot belong to one of them. MLflow's
artifact directory, `mlruns/`, is never observed.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope digits
cat hiraku/snakemake/Snakefile
```

Hiraku writes one rule per observed step. Each rule repeats the observed
arguments, so the workflow also trains for two epochs:

```python
rule weak_l2__train:
    input:
        "train.py",
    output:
        "hiraku/snakemake/logs/weak_l2__train.log",
    shell:
        "hiraku launch train --record weak-l2 -- --epochs 2 " + config.get('train', '') + " | tee {output}"
```

The steps wrote no file of their own, so each keeps its printed output as a
log. Both steps run and log two new MLflow runs, and the workflow run is
`succeeded`.

### 5. Run it again

```bash
hiraku snakemake run --record pipeline --scope digits
```

Nothing changed, so nothing runs: `no-op`.

## Path B: write the Snakefile

### 6. Write the rules

```bash
cat > Snakefile <<'EOF'
# Train both Digits regularization variants.
# Pass --config 'train=--epochs 2' for a quick run.

rule all:
    input: "logs/weak-l2.log", "logs/strong-l2.log"

rule weak_l2__train:
    input: "train.py"
    output: "logs/weak-l2.log"
    shell: "hiraku launch train --record weak-l2 -- " + config.get("train", "") + " > {output}"

rule strong_l2__train:
    input: "train.py"
    output: "logs/strong-l2.log"
    shell: "hiraku launch train --record strong-l2 -- " + config.get("train", "") + " > {output}"
EOF
git add Snakefile
git commit -m "add the Digits Snakefile"
```

The written rules do not fix the epochs. `config.get("train", "")` adds
whatever `--config train=...` passes to every `train` rule, so one setting
reaches both steps.

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline --config 'train=--epochs 2'
hiraku snakemake run --record pipeline --config 'train=--epochs 2'
```

The dry run is `planned`. The next run creates both logs, training each
variant for two epochs (`succeeded`); its recorded command ends with
`--epochs 2`. The last run is a `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku runs --record digits --all --metrics val/log_loss,val/accuracy
hiraku dashboard --record digits
```

The dashboard shows `pipeline` with its CSV workflow runs next to the
experiments' MLflow runs. To follow **Open in mlflow** links, see the
[Digits MLflow example](../digits-mlflow/README.md).

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Epochs | Fixed to the observed `--epochs 2` | Set per run with `--config` |
| Each step's file | `hiraku/snakemake/logs/<rule>.log`, also printed | `logs/<record>.log` |
