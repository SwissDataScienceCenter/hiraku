# Hiraku + Snakemake: a practical guide

Hiraku keeps the commands and results of your experiments. Snakemake connects
those commands into a workflow: it works out which steps must run from the
files they read and produce.

This guide describes the current Snakemake integration branch.

## The main pieces

| Term | Meaning |
| --- | --- |
| Record | A named task or experiment, such as `digits` or `weak-l2`. |
| Entrypoint | A command stored on a record, such as `train`. Run it directly with `hiraku launch train --record weak-l2`. |
| Rule | A workflow step with input files, output files, and a command. The examples use rules that launch Hiraku entrypoints. |
| Snakefile | The file containing the rules. Snakemake uses it to order the steps and decide what to rebuild. |
| Workflow run | One execution of the workflow, with information about its steps and files. |

The workflow record uses a **CSV backend**. Its steps can keep their own
tracking backends, such as MLflow or W&B. Give workflow runs a separate file,
such as `hiraku/runs.csv`, so their columns do not interfere with experiment
scripts that append to another CSV file.

## Install

You need Git, [uv](https://docs.astral.sh/uv/), and this Hiraku branch. Run the
following from the Hiraku repository checkout:

```bash
uv tool install --force --python 3.12 '.[mlflow,wandb,dashboard,snakemake]'
hiraku snakemake --help
```

This installs the extras used across the examples. Python 3.12 is recommended
for compatibility with Snakemake's dependencies. Keep `hiraku` available on
your `PATH`: the workflow rules call it too.

## Run the Digits example

This example uses a small dataset bundled with scikit-learn. It prepares the
data, then trains two models with different regularization strengths. Each
training step writes a result file and logs its metrics to local MLflow.
No dataset download, tracking server, or account is needed.

### 1. Create a project

Create it in a new directory outside the Hiraku checkout:

```bash
mkdir digits-demo
cd digits-demo
git init
hiraku init --example digits-snakemake
uv sync --python 3.12
git add .
git commit -m "initialize Digits example"
```

`hiraku init` copies the scripts, Snakefile, and dependency file. `uv sync`
installs the example's dependencies into its own environment.

### 2. Create the workflow and experiment records

```bash
hiraku record create digits --type task \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}' \
  --set 'entrypoints={prepare: {command: [python, scripts/prepare_data.py]}}'

hiraku record create weak-l2 --parent digits --type experiment \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}' \
  --set 'entrypoints={train: {command: [python, scripts/train.py], args: {alpha: 0.0001, epochs: 40, hidden-units: 64, seed: 42}}}'

hiraku record create strong-l2 --parent digits --type experiment \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}' \
  --set 'entrypoints={train: {command: [python, scripts/train.py], args: {alpha: 1.0, epochs: 40, hidden-units: 64, seed: 42}}}'

git add .
git commit -m "add workflow and experiment records"
```

The bundled Snakefile connects `digits`'s `prepare` entrypoint to both `train`
entrypoints. You can inspect a step with `hiraku record show weak-l2`.

### 3. Preview, then run

```bash
hiraku snakemake run --record digits --dry-run --config 'train=--epochs 2' \
  --command uv --command-arg run --command-arg snakemake

hiraku snakemake run --record digits --config 'train=--epochs 2' \
  --command uv --command-arg run --command-arg snakemake
```

The first command records a plan without training. The second runs Snakemake
inside the example's environment and trains for two epochs. Remove
`--config 'train=--epochs 2'` to use the records' 40-epoch setting.

Repeat the second command unchanged: with the outputs up to date, the new
workflow run should have outcome `no-op`.

### 4. Inspect the results

```bash
hiraku runs --record digits --json
hiraku runs --record weak-l2 --json
hiraku runs --record strong-l2 --json
hiraku dashboard --record digits
```

- `results/weak-l2.json` and `results/strong-l2.json` contain the model results.
- `hiraku/runs.csv` contains the workflow runs; `mlflow.db` contains training runs.
- Select a workflow run in the dashboard to see its steps. **Files** shows which
  steps produced and used each file.
- Each training run stores `hiraku.workflow_run_id` as an MLflow tag. The
  result JSON also contains that ID, linking training to its workflow run.

## Choose how to define the workflow

**Use a written Snakefile** when you want explicit, reviewable rules to share.
The Digits example already includes one. For your own project, start a draft:

```bash
hiraku snakemake generate --record digits --print
```

Without `--print`, this writes `Snakefile` and refuses to overwrite an existing
file. A draft has one rule per entrypoint of the record and its children.
Fill in its inputs and outputs, and add a `rule all` listing the desired final
files. Otherwise, Snakemake defaults to the first rule.

**Use observed launches** when you want Hiraku to infer files from steps you
run yourself. In the Digits project above:

```bash
uv run hiraku launch prepare --record digits --observe -- --output data/digits.npz
uv run hiraku launch train --record weak-l2 --observe -- \
  --input data/digits.npz --output results/weak-l2.json --epochs 2
uv run hiraku launch train --record strong-l2 --observe -- \
  --input data/digits.npz --output results/strong-l2.json --epochs 2

hiraku snakemake run --record digits --observed \
  --command uv --command-arg run --command-arg snakemake
```

These launches **execute the steps**, then save their observed inputs and
changed outputs. `--observed` builds rules from them even when a Snakefile
exists. The workflow may rebuild files if the rules changed, or record a
`no-op` if everything is up to date.

Observations and their generated Snakefile live under `hiraku/snakemake/`
and are ignored by Git. To turn them into a file you can review and commit:

```bash
hiraku snakemake generate --record digits --observed --output Snakefile.observed
```

By default, `run` uses `./Snakefile` if present and observed launches otherwise.
If the step records are outside the workflow record's children, select their
parent with `--scope TASK` when using observations.

## Command options

### `hiraku snakemake run --record RECORD [OPTIONS] [TARGETS...]`

| Option | What it does |
| --- | --- |
| `--record RECORD` | Required. The record whose CSV backend stores the workflow run. |
| `--snakefile PATH` | Use a particular workflow file. |
| `--observed` | Build rules from observed launches. Cannot be combined with `--snakefile`. |
| `--scope RECORD` | For observed rules, use this record and its children as the source of steps. |
| `--cores N` | Allow up to N local CPU cores; default: 1. |
| `--dry-run` | Record planned jobs without executing them. |
| `--config KEY=VALUE` | Pass Snakemake configuration. Repeat for multiple values. A rule must use the setting for it to affect a step. |
| `--force` | Force the requested target to run. |
| `--forceall` | Force all steps needed by the requested targets to run. |
| `--keep-going` | Continue independent steps if another step fails. |
| `--command EXECUTABLE` | Choose the launcher; default: `snakemake`. |
| `--command-arg ARG` | Repeat to add launcher arguments. For `uv run snakemake`, use `--command uv --command-arg run --command-arg snakemake`. |
| `TARGETS` | Rule names or output files to build, such as `results/weak-l2.json`. |
| `--help` | Show the command reference. |

### `hiraku snakemake generate --record RECORD [OPTIONS]`

| Option | What it does |
| --- | --- |
| `--record RECORD` | Required. The record whose entrypoints and children form the draft. |
| `--observed` | Generate rules with files and arguments from observed launches. |
| `--scope RECORD` | Select another record and its children; requires `--observed`. |
| `--output PATH` | Destination; default: `Snakefile` in the project root. |
| `--print` | Print the draft without saving it. |
| `--force` | Overwrite the destination if it exists. |
| `--help` | Show the command reference. |

To observe a single step, use `hiraku launch ENTRYPOINT --record RECORD --observe`.
Arguments after `--` go to the step's command. Alternatively, set
`HIRAKU_OBSERVE=1` for a session, or use `observe=True` in the Python launch API
or the MCP `launch_entrypoint` tool.

## What the evidence means

Each workflow execution starts as a `started` CSV row and is updated with its
outcome: `planned`, `succeeded`, `no-op`, `failed`, or `interrupted`. These are
execution outcomes, separate from a record's lifecycle status.

In `hiraku runs --json`, `raw.jobs` is a JSON-encoded string listing rules,
commands, record and entrypoint IDs, inputs, outputs, and `ran`. When reading
it in Python, use `json.loads(run["raw"]["jobs"])`. The integration marks a step as
having run when one of its outputs changed; a successful execution with no
such changes is a `no-op`. File observations are evidence, not content hashes
or a complete history of everything the program did.

Keep these limits in mind:

- Execution is local and synchronous. Remote and scheduler execution are not supported.
- Checkpoints and remote storage are outside the supported scope. A Snakefile's
  Python code can execute even during a dry run.
- Observed reads cover Python file access and paths named in commands.
  Other programs can read files observation does not detect.
- Only the latest successful observation of each record/entrypoint is kept.
  To retain two argument variants, use separate records or written rules.
- Shared tracking files are excluded from observed step outputs. A step with
  no output of its own gets a log file instead. Projects over 100,000 files
  and launches through dispatchers that execute elsewhere are not observed.
- Snakemake may not know a step's command, record, or entrypoint until it has
  built the file itself. Those fields can therefore be empty in planned runs.
- Tracking `hiraku/` in the code repository makes new evidence appear as Git
  changes. Ignoring it or versioning it separately keeps the code checkout clean.

## More examples

Each linked walkthrough gives the setup commands, observed-launch route,
written-Snakefile route, and result checks. Start each in a separate directory.

| Example | What to try |
| --- | --- |
| [Minimal project](docs/examples/snakemake-pipelines/minimal.md) | Run the same entrypoint with two seeds. |
| [Heart Disease](docs/examples/snakemake-pipelines/heart-disease.md) | Combine three training steps that log to CSV. |
| [Digits with MLflow](docs/examples/snakemake-pipelines/digits-mlflow.md) | Add a workflow to existing MLflow experiments. |
| [Iris with W&B](docs/examples/snakemake-pipelines/iris-wandb.md) | Track runs offline without a W&B account. |
| [EuroSAT RGB](docs/examples/snakemake-pipelines/eurosat-rgb.md) | Download a dataset, then train two models. |
| [Workflow examples](docs/examples/snakemake-pipelines/workflows.md) | Chain three snapshot steps into a report. |
| [Autoresearch](docs/examples/snakemake-pipelines/autoresearch.md) | Use the CPU mock and declare ordering explicitly. |
| [Full Digits walkthrough](docs/examples/digits-snakemake/README.md) | Explore the bundled workflow and the MLflow UI. |

For further detail, see the [Snakemake reference guide](docs/guides/tracking-snakemake.md).
