# EuroSAT RGB as a Snakemake pipeline

This page adds a download step to the
[EuroSAT RGB example](../eurosat-rgb/README.md) and trains the MLP and the CNN
on the downloaded images in one workflow. It shows how a step that writes
thousands of files is declared.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `eurosat__download` | `eurosat` · `download` | `scripts/download.py` | `data/eurosat/`, about 27,000 images |
| `mlp__train` | `mlp` · `train` | `data/eurosat/`, `train.py` | a W&B run |
| `cnn__train` | `cnn` · `train` | `data/eurosat/`, `train.py` | a W&B run |

The download is about 90 MB, and training runs on the CPU, so this page passes
`--epochs 1` for a quick run.

## 1. Create the project with its records

```bash
mkdir eurosat-pipeline
cd eurosat-pipeline
git init
hiraku init --example eurosat-rgb
uv sync
git add .
git commit -m "initialize EuroSAT RGB example"
export WANDB_MODE=offline
hiraku record create eurosat --type task \
  --set "goal=Maximize EuroSAT RGB validation accuracy" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}, {name: val/loss, direction: minimize}]' \
  --set 'tracking_backend={type: wandb, project: eurosat-rgb}'
for model in mlp cnn; do
  hiraku record create "$model" --parent eurosat --type experiment \
    --set "summary=$model baseline for EuroSAT RGB classification" \
    --commit HEAD \
    --set "entrypoints={train: {command: [uv, run, python, train.py], args: {model: $model, seed: 42, epochs: 10, batch-size: 64, learning-rate: 0.001, validation-fraction: 0.2}}}"
done
```

These are the example's records, shortened: a task using W&B, run offline
here as in the [Iris page](iris-wandb.md), and one experiment per model with a
`train` entrypoint.

## 2. Add the download step and the workflow record

```bash
mkdir -p scripts
cat > scripts/download.py <<'EOF'
"""Download EuroSAT RGB into data/ once, so the training steps share it."""

from torchvision import datasets

dataset = datasets.EuroSAT(root="data", download=True)
print(f"EuroSAT RGB: {len(dataset)} images in {len(dataset.classes)} classes")
EOF
hiraku record update eurosat \
  --set 'entrypoints={download: {command: [uv, run, python, scripts/download.py]}}'
hiraku record create pipeline --parent eurosat --type task \
  --set "summary=Download EuroSAT, then train the MLP and the CNN" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/logs/' '/data/' 'wandb/' >> .gitignore
git add .
git commit -m "add the download step and the pipeline record"
```

`train.py` downloads the dataset itself when it is missing. A separate
`download` step on the task record lets both training steps share one copy and
gives the workflow a first step. Git ignores the data.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
hiraku launch download --record eurosat --observe
hiraku launch train --record mlp --observe -- --epochs 1
hiraku launch train --record cnn --observe -- --epochs 1
cat hiraku/snakemake/observed/eurosat__download.json
```

The download creates about 27,000 files under the new directory
`data/eurosat`. Listing them all would make an unreadable rule, so Hiraku
declares more than 50 new files under one new directory as that directory:

```json
{
  "record": "eurosat",
  "entrypoint": "download",
  "options": [],
  "arguments": [],
  "inputs": ["scripts/download.py"],
  "outputs": ["data/eurosat/"],
  "observed_at": "..."
}
```

The trailing `/` marks a directory. Each training step opens thousands of
images under `data/eurosat`; they are grouped by folder in its observation.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope eurosat
cat hiraku/snakemake/Snakefile
```

The training rules depend on the download through the directory:

```python
rule eurosat__download:
    input:
        "scripts/download.py",
    output:
        directory("data/eurosat"),
    shell:
        "hiraku launch download --record eurosat -- " + config.get('download', '')


rule mlp__train:
    input:
        "data/eurosat",
        "train.py",
    output:
        "hiraku/snakemake/logs/mlp__train.log",
    shell:
        "hiraku launch train --record mlp -- --epochs 1 " + config.get('train', '') + " | tee {output}"
```

`data/eurosat` already exists, so Snakemake does not download again. It trains
both models, which have no log yet, and the workflow run is `succeeded`.

### 5. Run it again

```bash
hiraku snakemake run --record pipeline --scope eurosat
```

Nothing changed: `no-op`. Delete `data/eurosat` and the next run downloads
the dataset and trains both models again.

## Path B: write the Snakefile

### 6. Write the rules

```bash
cat > Snakefile <<'EOF'
# Download EuroSAT RGB once, then train the MLP and the CNN on it.
# Pass --config 'train=--epochs 1' for a quick CPU run.

rule all:
    input: "logs/mlp.log", "logs/cnn.log"

rule eurosat__download:
    input: "scripts/download.py"
    output: directory("data/eurosat")
    shell: "hiraku launch download --record eurosat"

rule mlp__train:
    input: "data/eurosat", "train.py"
    output: "logs/mlp.log"
    shell: "hiraku launch train --record mlp -- " + config.get("train", "") + " > {output}"

rule cnn__train:
    input: "data/eurosat", "train.py"
    output: "logs/cnn.log"
    shell: "hiraku launch train --record cnn -- " + config.get("train", "") + " > {output}"
EOF
git add Snakefile
git commit -m "add the EuroSAT Snakefile"
```

`directory("data/eurosat")` tells Snakemake that the download produces a whole
directory, the same declaration observation found.

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline --config 'train=--epochs 1'
hiraku snakemake run --record pipeline --config 'train=--epochs 1'
```

`planned`, then `succeeded` (both models train for one epoch; the dataset is
already there), then `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku dashboard --record eurosat
```

In the dashboard, select a workflow run to see the download and both training
steps. **Files** shows `data/eurosat` used by both training steps. Its producer
appears as `rule eurosat__download`, without a record, because the observed
launch in step 3 downloaded it rather than Snakemake; after a run that
downloads again, it shows `eurosat` · `download`.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Epochs | Fixed to the observed `--epochs 1` | Set per run with `--config` |
| Each training step's file | `hiraku/snakemake/logs/<rule>.log` | `logs/<model>.log` |

The download rule is the same in both.
