# Track Snakemake workflows

Hiraku runs a Snakemake workflow whose rules launch record entrypoints. The two
files split the work:

- **Records state the code.** Each step is an ordinary entrypoint, a `command`
  and its `args`, on the record it belongs to.
- **The `Snakefile` states the files.** Each rule declares `input:` and
  `output:` and runs `hiraku launch ENTRYPOINT --record RECORD`, passing the
  files after `--`.

Nothing in the record or run format is specific to Snakemake. Every step can
still be run on its own with `hiraku launch`.

## Write the records and the Snakefile

Give the workflow record a CSV tracking backend whose file lives with the
records, and give each record the entrypoints of its steps:

```bash
hiraku record create workflow --type task \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}' \
  --set 'entrypoints={prepare: {command: [python, prepare.py]}}'
hiraku record create model --parent workflow --type experiment \
  --set 'entrypoints={train: {command: [python, train.py], args: {epochs: 40}}}'
```

Draft a `Snakefile` with one rule per entrypoint of the record and the records
below it:

```bash
hiraku snakemake generate --record workflow
```

The draft names each rule `<record>__<entrypoint>`. Add each rule's files and a
`rule all`:

```python
rule all:
    input: "model.pkl"

rule workflow__prepare:
    input: "prepare.py"
    output: "data.csv"
    shell: "hiraku launch prepare --record workflow -- --output {output}"

rule model__train:
    input: "data.csv", "train.py"
    output: "model.pkl"
    shell:
        "hiraku launch train --record model -- --input {input[0]} --output {output} "
        + config.get("train", "")
```

`--print` shows the draft without writing it, `--output` selects another path,
and `--force` overwrites an existing file.

### Steps whose code writes no file of its own

Snakemake needs every step to produce files it owns. The best result file is
one written by the step's code, so give the script an option such as
`--output` and pass `{output}` to it, as above. Some scripts only append a row
to a shared file, such as the project-root `runs.csv`. That file cannot be the
output of several steps, so capture the launch's printed output instead:

```python
# The script writes its result to the declared file.
rule model__train:
    input: "data.csv", "train.py"
    output: "results/model.json"
    shell: "hiraku launch train --record model -- --output {output}"

# The script only appends to runs.csv; its printed output is the step's file.
rule model__train:
    input: "data.csv", "train.py"
    output: "logs/model.log"
    shell: "hiraku launch train --record model > {output}"
```

Both are recorded the same way: the job names the record, entrypoint, and
command, and `ran` is true when the file changed. They differ in what the file
holds:

| | With `--output` | Without, capturing the output |
| --- | --- | --- |
| Step's file | The result, for example metrics and the run ID | Whatever the script prints, possibly nothing |
| Later steps | Can read the result as an `input:` | Cannot use the result |
| Files view | Shows which record produced the result | Shows which record produced the log |
| Where the result lives | In the file and in the step's tracked run | Only in the tracked run, a row of `runs.csv` |

The [Digits example](../examples/digits-snakemake/README.md) uses `--output`
in every step.

## Run the workflow

```bash
hiraku snakemake run --record workflow
hiraku runs --record workflow --json
hiraku dashboard --record workflow
```

The command needs a `snakemake` executable on `PATH` (`uv tool install
'hiraku[snakemake]'`), or `--command uv --command-arg run --command-arg
snakemake` to run Snakemake in the project environment. `hiraku` must be on
`PATH` inside the rules.

Pass extra entrypoint arguments through Snakemake's config, for example to
make a quick run:

```bash
hiraku snakemake run --record workflow --config 'train=--epochs 2'
```

Snakemake runs synchronously and locally from the project root. Options that
change the working directory or hand execution to a scheduler, profile,
cluster, or remote backend are rejected. `--dry-run` records the planned jobs
without running them.

Launched entrypoints get the usual `hiraku launch` attribution, plus
`HIRAKU_WORKFLOW_RUN_ID`. The training code can store that ID with its own
runs, for example as an MLflow tag, to link them to the workflow execution.

## Stored evidence

Each execution is one run of the workflow record in its CSV file. The row is
written as `started` before Snakemake runs and replaced when it finishes, so an
interrupted execution still leaves evidence. Rows and columns already in the
file are kept.

Keep the file under `hiraku/`, as in the example above, rather than in the
default project-root `runs.csv`. The evidence then follows however the records
are versioned: when `hiraku/` is ignored by Git or kept in its own repository,
recording an execution leaves the code checkout clean, so the next execution and
its steps still report `git_state: clean`. A separate file also keeps the extra
columns away from scripts that append to `runs.csv` with a fixed header.

| Column | Value |
| --- | --- |
| `commit`, `timestamp`, `entrypoint` | The executed `HEAD`, the start time, and `snakemake` |
| `param.command` | The Snakemake command line |
| `param.snakefile_sha256` | The Snakefile content digest |
| `outcome` | `started`, `planned`, `succeeded`, `no-op`, `failed`, or `interrupted` |
| `ended`, `git_state` | The end time, and `clean`, `dirty`, or `unknown` |
| `returncode`, `diagnostic` | The exit code and the end of the output on failure |
| `jobs` | A JSON list with one entry per step |

Each entry in `jobs` describes one step:

```json
{"rule": "model__train", "record": "model", "entrypoint": "train",
 "command": "hiraku launch train --record model -- --input data.csv --output model.pkl",
 "inputs": ["data.csv", "train.py"], "outputs": ["model.pkl"], "ran": true}
```

The entries come from `snakemake --detailed-summary`, which reports each output
file with the rule, inputs, and shell command that last built it. `record` and
`entrypoint` are read from the `hiraku launch` command and stay `null` for rules
that run other commands. `ran` is true when one of the step's outputs changed
during this execution. An execution in which no step ran is a `no-op`.
Snakemake reports a command only after it has built the file, so jobs of a
planned run that were never built show `null`.
