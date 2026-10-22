# Track Snakemake workflows

Hiraku runs a Snakemake workflow whose steps are record entrypoints. Records
state each step's code and arguments; the rules connect the steps through the
files they read and write. There are two ways to get the rules:

- **Observed, with no `Snakefile`.** Run each step once with
  `hiraku launch --observe`, then run `hiraku snakemake run`. Hiraku writes the
  rules from the files each launch read and wrote. There is nothing to edit.
- **A written `Snakefile`.** Written by an agent, or by you, when the workflow
  should be reviewed, shared, or needs rules Hiraku cannot observe, such as
  wildcards. `hiraku snakemake generate` drafts it; see
  [Why a written Snakefile suits agents](#why-a-written-snakefile-suits-agents).

`hiraku snakemake run` uses `./Snakefile` when it exists and the observed
launches otherwise. Nothing in the record or run format is specific to
Snakemake, every step can still be run on its own with `hiraku launch`, and a
project that never runs Snakemake is unaffected.

| Step | Observed | Written `Snakefile` |
| --- | --- | --- |
| An entrypoint for each step | You, as without Snakemake | You, as without Snakemake |
| A CSV tracking backend on the workflow record | Only if the project uses another backend | Only if the project uses another backend |
| Each step's files | Hiraku, from one `hiraku launch --observe` of the step | You or an agent, as `input:` and `output:` |
| The rules and `rule all` | Hiraku, on every `hiraku snakemake run` | You or an agent; `generate` drafts them, `generate --observed` fills them in |
| Running Snakemake and storing one run with an entry per step | Hiraku | Hiraku |
| Linking each step's own tracked run to the workflow run | Optional | Optional |

## Set up the records

Each step is an entrypoint of the record it belongs to, written exactly as for
`hiraku launch`, so records that already have entrypoints need no change. The
workflow record must use a CSV tracking backend. CSV is the project default,
and setting its file under `hiraku/` is recommended (see
[Stored evidence](#stored-evidence)); the step records keep their own backends,
such as MLflow:

```bash
hiraku record create workflow --type task \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}' \
  --set 'entrypoints={prepare: {command: [python, prepare.py]}}'
hiraku record create model --parent workflow --type experiment \
  --set 'entrypoints={train: {command: [python, train.py], args: {epochs: 40}}}'
```

## Without a Snakefile: observe the launches

Launch each step once with `--observe`, then run the workflow:

```bash
hiraku launch prepare --record workflow --observe -- --output data.csv
hiraku launch train --record model --observe -- --input data.csv --output model.pkl
hiraku snakemake run --record workflow
```

Observation is opt-in: a plain `hiraku launch` behaves as on main. Set
`HIRAKU_OBSERVE=1` to observe every launch of a session, and pass
`observe=True` to `hiraku.launch.launch` or the MCP `launch_entrypoint` tool.
An observed launch notes its call-time arguments and the project files it
used:

- **Outputs** are the files the launch created or changed.
- **Inputs** are the files that existed before and that a Python process opened
  for reading, or that the command names, such as the script.

`hiraku snakemake run` then writes `hiraku/snakemake/Snakefile` with one rule
per observed step of the workflow record and the records below it. Each rule
repeats the step's `--dispatch` or `--commit` and its arguments, declares its
files, and `rule all` asks for every file no other step reads. More than 50
files that a step creates under one new directory, such as a downloaded
dataset, are declared as that `directory()`, and the steps reading them depend
on it.

When the steps are not below the workflow record, for example when the
experiments use MLflow and a separate CSV `pipeline` record holds the workflow
runs, name the record above the steps with `--scope`:

```bash
hiraku snakemake run --record pipeline --scope my-task
``` Snakemake runs
the steps in dependency order, and only those whose inputs or rules changed.
To change a step's rule, launch the step again with `--observe`; the latest
successful observed launch of each record and entrypoint wins, so one
entrypoint launched with two argument sets, such as two seeds, becomes one
rule. Write a `Snakefile` to keep both.

What observation leaves out:

| Case | What happens | If you need it |
| --- | --- | --- |
| A non-Python program reads a file the command does not name | Not an input | Pass the path as an argument, or write a `Snakefile` |
| A file several steps write, the record's tracking file (its CSV file or MLflow SQLite database), `mlruns/`, or `wandb/` | Not a step file | Nothing; Snakemake does not own these |
| A step that writes no file of its own | Its printed output becomes `hiraku/snakemake/logs/<rule>.log` | Give the script an output file |
| `.git`, virtual environments, caches, `hiraku/` | Never observed | Nothing |
| More than 100,000 files in the project | The launch is not observed | Write a `Snakefile` |
| A dispatcher that runs the command elsewhere, such as `worktree` | Nothing is observed | Observe a launch with the default dispatcher |

Observations stay on your machine: `hiraku/snakemake/` ignores itself in Git,
so an observed launch never dirties the checkout. Collaborators observe the
steps by launching them, or you commit a `Snakefile` written with
`hiraku snakemake generate --observed`. Launches made by `hiraku snakemake run`
are never observed, since their files are already declared.

## Why a written Snakefile suits agents

Observation spares a person from writing rules. An agent working through the
Hiraku CLI or MCP server is better served by writing the `Snakefile` itself:

- **It has the facts already.** The records state each step's command and
  arguments (`hiraku record show`), the scripts' argument parsers name their
  files, and `generate` (or `generate --observed`) gives the rule skeletons.
  Writing the rules is a small, declarative edit.
- **The edit cannot change the science.** A rule only wires files to a
  `hiraku launch` of a record's entrypoint; the code and arguments stay in the
  record. A wrong rule misorders or misses a step, and never runs different code
  under the record's name.
- **It is checked before anything runs.** `hiraku snakemake run --dry-run`
  builds the DAG and records the planned jobs, so a missing input or a cycle
  costs no compute.
- **The result is evidence to check against.** Each run's `jobs` lists what
  every rule ran, read, and wrote, so the agent can compare the execution with
  its intent from `hiraku runs --json`.
- **It is reviewable and shared.** The `Snakefile` is one file in the
  repository: a person reviews the agent's change as a diff, and collaborators
  and CI run the same rules. Observations stay on one machine.
- **It expresses what observation cannot**: wildcards over variants,
  non-Python steps, and files a step will produce but has not yet.

## With a Snakefile: write the rules

Write the observed rules to `./Snakefile` as a starting point, or draft one
rule per entrypoint of the record and the records below it:

```bash
hiraku snakemake generate --record workflow --observed  # complete rules
hiraku snakemake generate --record workflow             # files left to fill in
```

The draft names each rule `<record>__<entrypoint>`, launches its entrypoint,
and leaves `input:` and `output:` commented out. Fill in each rule's files and
add a `rule all`, or pass the targets to `hiraku snakemake run`; otherwise
Snakemake builds only the first rule:

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

`--print` shows the file without writing it, `--output` selects another path,
and `--force` overwrites an existing file. `hiraku snakemake run` accepts any
`Snakefile`, and fills in `record` and `entrypoint` for the rules whose
`shell:` runs `hiraku launch ENTRYPOINT --record RECORD`.

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

`--snakefile` selects another file, and `--observed` uses the observed
launches even when `./Snakefile` exists. Snakemake runs synchronously and
locally from the project root. Options that
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
