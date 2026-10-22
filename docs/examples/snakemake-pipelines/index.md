# Snakemake pipelines for every example

Every example in this documentation can run its steps as one Snakemake
workflow. Each page below starts from a new project, adds a workflow to one
example, and ends in the dashboard. It explains what happens at every step.
The [Snakemake guide](../../guides/tracking-snakemake.md) is the reference for
the commands and the stored columns.

## The three pieces

1. **Each step is an entrypoint.** A step runs
   `hiraku launch ENTRYPOINT --record RECORD`, the same command you would type
   by hand. The record keeps the step's command and arguments, so the workflow
   never changes which code a record runs.
2. **A workflow record keeps the evidence.** On these pages it is a task
   record named `pipeline`, next to the experiments, with
   `tracking_backend: {type: csv, path: hiraku/runs.csv}`. Every
   `hiraku snakemake run` adds one run to it, with one entry per step. It is a
   separate record because the experiments keep their own backends (CSV,
   MLflow, or W&B), and the workflow runs need a CSV file of their own.
3. **Rules connect the steps through files.** A rule declares which files a
   step reads and writes, so Snakemake knows the order and what to rerun. You
   get the rules in one of two ways.

## Two ways to get the rules

| | Path A: observed launches | Path B: a written `Snakefile` |
| --- | --- | --- |
| What you do | Launch each step once with `hiraku launch --observe`, then `hiraku snakemake run` | Write `Snakefile`, then `hiraku snakemake run` |
| Where the rules are | `hiraku/snakemake/Snakefile`, regenerated on every run and ignored by Git | `Snakefile`, committed with the code |
| A step's inputs | The files its Python process opened, and the paths its command names | The files you declare |
| Suits | Running your own steps without writing a file | Agents, review, sharing, and rules observation cannot see |

Each page follows Path A and then Path B in the same project. Once
`Snakefile` exists, `hiraku snakemake run` uses it instead of the observations.
You can also follow only one path.

When the steps are not below the workflow record, as with `pipeline` here,
Path A names the record above them with `--scope`, for example
`hiraku snakemake run --record pipeline --scope heart-disease`.

## Before you start

Install Hiraku from the repository checkout with the extras the pages use, so
that `hiraku` and `snakemake` are on your `PATH`:

```bash
uv tool install --force --python 3.12 '.[mlflow,wandb,dashboard,snakemake]'
```

Snakemake depends on `immutables`, which may fail to build on Python versions
without a prebuilt wheel; Python 3.12 has one.

## The pages

| Example | Steps | What it shows |
| --- | --- | --- |
| [Heart Disease](heart-disease.md) | 3 × `train` | Steps whose only output is their tracking row |
| [Digits with MLflow](digits-mlflow.md) | 2 × `train` | A shared MLflow database is not a step file |
| [Start from scratch](minimal.md) | 2 × `evaluate` | One entrypoint launched with two seeds |
| [Iris with W&B](iris-wandb.md) | 2 × `train` | W&B runs offline, without an account |
| [EuroSAT RGB](eurosat-rgb.md) | `download` → 2 × `train` | A downloaded dataset becomes one `directory()` output |
| [Workflow examples](workflows.md) | 3 × `snapshot` → `report` | Steps chained by the files they pass on |
| [Autoresearch](autoresearch.md) | `prepare`, `mock` | A dispatcher choice, and an order only a written rule states |
| [Digits with Snakemake](../digits-snakemake/README.md) | `prepare` → 2 × `train` | The bundled example, with its own `Snakefile` |

## What to look at

Each page ends with the same three checks:

- **`hiraku runs --record pipeline`** lists the workflow runs. `outcome` is
  `planned` for a dry run, `succeeded` when a step ran, and `no-op` when
  nothing needed to run.
- **`hiraku runs --record pipeline --json`** shows `raw.jobs` for each run:
  every step's rule, record, entrypoint, command, inputs, outputs, and whether
  it ran.
- **`hiraku dashboard`** shows the `pipeline` runs next to the experiments'
  runs. Select a workflow run to see its steps, and open **Files** to see which
  step produced each file and which steps used it.

A file that a manual or observed launch built, rather than Snakemake, shows
only its rule as the producer: Snakemake reports the command behind a file once
it has built the file itself. In examples whose `.gitignore` does not ignore
`hiraku/`, the workflow runs in `hiraku/runs.csv` show up in `git status`;
commit them, or ignore `hiraku/` as the
[guide](../../guides/tracking-snakemake.md#stored-evidence) explains.
