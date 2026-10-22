# Start from scratch as a Snakemake pipeline

This page runs the baseline of the
[minimal project](../minimal/README.md) with two seeds in one workflow. It
shows the main limit of observation: one record entrypoint becomes one rule.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `baseline__evaluate_seed0` | `baseline` · `evaluate --seed 0` | `evaluate.py` | a row of `runs.csv` |
| `baseline__evaluate_seed1` | `baseline` · `evaluate --seed 1` | `evaluate.py` | a row of `runs.csv` |

## 1. Create the project with its records

```bash
mkdir minimal-pipeline
cd minimal-pipeline
git init
hiraku init --example minimal
git add .
git commit -m "initial commit"
hiraku record create my-task --type task \
  --set "goal=Improve validation accuracy on my dataset" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'
hiraku record create baseline --parent my-task --type experiment \
  --set "hypothesis=The existing representation establishes a reference." \
  --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py], args: {seed: 0}}}'
```

These are the records of the minimal project: a task and a `baseline`
experiment bound to the current commit, whose `evaluate` entrypoint runs
`evaluate.py --seed 0`. `evaluate.py` appends its result to the project-root
`runs.csv`.

## 2. Add the workflow record

```bash
hiraku record create pipeline --parent my-task --type task \
  --set "summary=Evaluate the baseline with two seeds" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/logs/' >> .gitignore
git add .
git commit -m "add the pipeline record"
```

`pipeline` keeps the workflow runs in `hiraku/runs.csv`, apart from the
evaluation rows in `runs.csv`.

## Path A: observe the steps

### 3. Launch both seeds with `--observe`

```bash
hiraku launch evaluate --record baseline --observe -- --seed 0
hiraku launch evaluate --record baseline --observe -- --seed 1
ls hiraku/snakemake/observed/
```

Both launches evaluate and add a row to `runs.csv`. There is only one
observation, `baseline__evaluate.json`: Hiraku keeps the latest successful
observed launch of each record and entrypoint, here the one with `--seed 1`.
A later launch with other arguments replaces the earlier one, so a rule always
follows what you ran last.

### 4. Run the workflow from the observation

```bash
hiraku snakemake run --record pipeline --scope my-task
cat hiraku/snakemake/Snakefile
```

The generated file has a single rule:

```python
rule baseline__evaluate:
    input:
        "evaluate.py",
    output:
        "hiraku/snakemake/logs/baseline__evaluate.log",
    shell:
        "hiraku launch evaluate --record baseline -- --seed 1 " + config.get('evaluate', '') + " | tee {output}"
```

It evaluates seed 1 and records a `succeeded` workflow run. Seed 0 is not part
of this workflow. To run both seeds, write the rules, as in Path B.

### 5. Run it again

```bash
hiraku snakemake run --record pipeline --scope my-task
```

Nothing changed: `no-op`.

## Path B: write the Snakefile

### 6. Write one rule per seed

```bash
cat > Snakefile <<'EOF'
# Evaluate the baseline with two seeds.

rule all:
    input: "logs/baseline-seed0.log", "logs/baseline-seed1.log"

rule baseline__evaluate_seed0:
    input: "evaluate.py"
    output: "logs/baseline-seed0.log"
    shell: "hiraku launch evaluate --record baseline -- --seed 0 > {output}"

rule baseline__evaluate_seed1:
    input: "evaluate.py"
    output: "logs/baseline-seed1.log"
    shell: "hiraku launch evaluate --record baseline -- --seed 1 > {output}"
EOF
git add Snakefile
git commit -m "add the two-seed Snakefile"
```

Two rules launch the same entrypoint with different arguments. Each needs its
own output file, so Snakemake can tell the two steps apart.

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline
hiraku snakemake run --record pipeline
```

The dry run plans two jobs. The next run evaluates both seeds (`succeeded`),
and the last is a `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku runs --record baseline
hiraku dashboard --record my-task
```

`hiraku runs --record baseline` lists every evaluation, from the manual
launches and from both workflows. In the dashboard, the Path B workflow run
lists two steps for `baseline` · `evaluate`, one per seed.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Rules | One, for the last observed seed | One per seed |
| Seeds evaluated | 1 | 0 and 1 |
