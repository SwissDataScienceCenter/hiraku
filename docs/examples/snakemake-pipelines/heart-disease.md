# Heart Disease as a Snakemake pipeline

This page trains the three experiments of the
[Heart Disease example](../heart-disease/README.md) in one workflow. The
steps do not depend on each other. The workflow runs all three and records
which ran.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `logistic_baseline__train` | `logistic-baseline` · `train` | `data/processed.cleveland.data`, `train.py` | a row of `runs.csv` |
| `random_forest__train` | `random-forest` · `train` | the same | a row of `runs.csv` |
| `forest_tuning__train` | `forest-tuning` · `train` | the same | a row of `runs.csv` |

## 1. Create the project

```bash
mkdir heart-disease-pipeline
cd heart-disease-pipeline
git init
hiraku init --example heart-disease --with-results
uv sync --python 3.12
git add .
git commit -m "initialize Heart Disease example with results"
```

`--with-results` copies the example's records and measured runs. The task
`heart-disease` has three experiments: `logistic-baseline`, `random-forest`,
and `forest-tuning`. Each has a `train` entrypoint that runs `train.py` with
its own model and arguments. `train.py` appends one row per run to the
project-root `runs.csv`, the CSV backend the records inherit. `uv sync`
installs the example's dependencies.

## 2. Add the workflow record

```bash
hiraku record create pipeline --parent heart-disease --type task \
  --set "summary=Train every Heart Disease experiment" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/logs/' >> .gitignore
git add .
git commit -m "add the pipeline record"
```

`pipeline` will hold one run per workflow execution. Its runs go to
`hiraku/runs.csv`, not `runs.csv`: `train.py` writes `runs.csv` with a fixed
header, and the workflow runs add columns such as `outcome` and `jobs`.
Snakemake keeps its own state in `.snakemake/`, and Path B writes logs to
`logs/`. Git ignores both. This example's `.gitignore` also ignores `hiraku/`,
so its records and workflow runs are hidden from Git. Files created or changed
by a launched command still follow the project's normal Git rules.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
hiraku launch train --record logistic-baseline --observe
hiraku launch train --record random-forest --observe
hiraku launch train --record forest-tuning --observe
cat hiraku/snakemake/observed/random-forest__train.json
```

Each launch trains the model exactly as without `--observe`, and adds its row
to `runs.csv`. `--observe` also makes Hiraku note the project files around the
launch:

- before and after the command, it compares every file's size and
  modification time, to find what the step wrote;
- while the command runs, it records every file a Python process opens for
  reading.

The result for `random-forest`:

```json
{
  "record": "random-forest",
  "entrypoint": "train",
  "options": [],
  "arguments": [],
  "inputs": ["data/processed.cleveland.data", "train.py"],
  "outputs": [],
  "observed_at": "..."
}
```

`train.py` was named in the command and the data file was opened by Python, so
both are inputs. There are no outputs: the only file the step changed is
`runs.csv`, the records' own tracking file, which is never a step file.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope heart-disease
cat hiraku/snakemake/Snakefile
```

There is no `Snakefile`, so Hiraku writes `hiraku/snakemake/Snakefile` from
the three observations and runs Snakemake on it. `--scope heart-disease` takes
the observed steps from `heart-disease` and the records below it; without it,
Hiraku would look only below `pipeline`. One of the generated rules:

```python
rule random_forest__train:
    input:
        "data/processed.cleveland.data",
        "train.py",
    output:
        "hiraku/snakemake/logs/random_forest__train.log",
    shell:
        "hiraku launch train --record random-forest -- " + config.get('train', '') + " | tee {output}"
```

Snakemake needs every step to produce a file it owns. These steps wrote none,
so each rule keeps the step's printed output as a log. Snakemake has never
built these logs, so it runs all three steps: three new training rows in
`runs.csv`. The workflow run is recorded on `pipeline` as `succeeded`.

### 5. Run it again, then change an input

```bash
hiraku snakemake run --record pipeline --scope heart-disease
echo "# Trained by the Snakemake pipeline." >> train.py
git commit -am "note the pipeline in train.py"
hiraku snakemake run --record pipeline --scope heart-disease
```

The second run finds every log up to date, runs nothing, and is recorded as
`no-op`. Changing `train.py` changes an input of all three steps, so the third
run trains them again and is `succeeded`. Snakemake compares the content of
small inputs, not only their times: `touch train.py` alone would still be a
`no-op`.

## Path B: write the Snakefile

### 6. Write the rules

```bash
cat > Snakefile <<'EOF'
# Train every Heart Disease experiment on the processed Cleveland data.

rule all:
    input: "logs/logistic-baseline.log", "logs/random-forest.log", "logs/forest-tuning.log"

rule logistic_baseline__train:
    input: "data/processed.cleveland.data", "train.py"
    output: "logs/logistic-baseline.log"
    shell: "hiraku launch train --record logistic-baseline > {output}"

rule random_forest__train:
    input: "data/processed.cleveland.data", "train.py"
    output: "logs/random-forest.log"
    shell: "hiraku launch train --record random-forest > {output}"

rule forest_tuning__train:
    input: "data/processed.cleveland.data", "train.py"
    output: "logs/forest-tuning.log"
    shell: "hiraku launch train --record forest-tuning > {output}"
EOF
git add Snakefile
git commit -m "add the Heart Disease Snakefile"
```

The same three steps, written by hand:

- `rule all` names the files the workflow should end with; Snakemake works
  back from them.
- `input:` and `output:` declare each step's files. Here they are the files the
  observation found, with the log under `logs/`.
- `shell:` launches the record's entrypoint, so the command and its arguments
  still come from the record.

`hiraku snakemake generate --record pipeline --scope heart-disease --observed`
would write the rules from step 4 to `Snakefile` instead, as a starting point
for editing.

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline
hiraku snakemake run --record pipeline
```

`./Snakefile` now exists, so `hiraku snakemake run` uses it and `--scope` is no
longer needed. The dry run builds the dependency graph and records the jobs it
would run as a `planned` run, without training. The next run creates the
three logs under `logs/`, so all three steps train again (`succeeded`). The
last run is a `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku runs --record heart-disease --all --sort val/log_loss
hiraku dashboard --record heart-disease
```

`hiraku runs --record pipeline` lists the six workflow runs, from both paths.
The second command lists every training run, including those the workflow
started. In the dashboard:

- the run table shows `pipeline` with its workflow runs next to the three
  experiments and their training runs;
- selecting a workflow run lists its steps, each with its record, entrypoint,
  and command, and marks the steps that did not run;
- **Files** shows `data/processed.cleveland.data` used by the three steps, and
  each log with the step that produced it.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| The rules | Generated in `hiraku/snakemake/Snakefile` | Written in `Snakefile` |
| Each step's file | `hiraku/snakemake/logs/<rule>.log`, also printed | `logs/<record>.log`, not printed |
| Inputs | Found by observation | Declared |

The steps, records, and commands are the same.
