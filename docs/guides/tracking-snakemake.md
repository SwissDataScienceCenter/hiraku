# Track Snakemake workflows

Hiraku runs a [Snakemake](https://snakemake.github.io) workflow whose steps are
record entrypoints, and stores each execution as a run of a workflow record.
This guide is the reference. The
[Snakemake pipelines](../examples/snakemake-pipelines/index.md) pages walk
through every example step by step.

## How it fits together

1. **Each step is an entrypoint.** A step runs
   `hiraku launch ENTRYPOINT --record RECORD`, the same command you would type
   by hand. The record keeps the base command and arguments; a rule can pass
   additional arguments to that entrypoint. Every step can still run on its own.
2. **A workflow record keeps the evidence.** Every `hiraku snakemake run` adds
   one CSV run to it, with one entry per step.
3. **Rules connect the steps through files.** A rule declares the files a step
   reads and writes, so Snakemake knows the order and what to rerun.

There are two ways to get the rules:

| | Observed launches | A written `Snakefile` |
| --- | --- | --- |
| What you do | Launch each step once with `hiraku launch --observe`, then `hiraku snakemake run` | Write `Snakefile`, then `hiraku snakemake run` |
| Where the rules are | `hiraku/snakemake/Snakefile`, regenerated on every run and ignored by Git | `Snakefile`, committed with the code |
| A step's inputs | The files its Python process opened, and the paths its command names | The files you declare |
| Suits | Running your own steps without writing a file | Agents, review, sharing, and rules observation cannot see |

`hiraku snakemake run` uses `./Snakefile` when it exists and the observed
launches otherwise. Nothing in the record or run format is specific to
Snakemake, and a project that never runs Snakemake is unaffected.

## Set up the records

Each step is an entrypoint of the record it belongs to, written exactly as for
`hiraku launch`, so records that already have entrypoints need no change.

The workflow record must use a CSV tracking backend. Give it its own file under
`hiraku/` (see [Stored evidence](#stored-evidence)). The step records keep
their own backends, such as MLflow:

```bash
hiraku record create workflow --type task \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}' \
  --set 'entrypoints={prepare: {command: [python, prepare.py]}}'
hiraku record create model --parent workflow --type experiment \
  --set 'entrypoints={train: {command: [python, train.py], args: {epochs: 40}}}'
```

Here the steps are `workflow` and the record below it. When the experiments
already exist under a task with another backend, add the workflow record next
to them instead, for example a `pipeline` task below that task, and name the
task with `--scope` when you observe (below).

## Observed launches

### Observe each step once

```bash
hiraku launch prepare --record workflow --observe -- --output data.csv
hiraku launch train --record model --observe -- --input data.csv --output model.pkl
hiraku snakemake run --record workflow
```

Observation is opt-in: a plain `hiraku launch` is unchanged. Set
`HIRAKU_OBSERVE=1` to observe every launch of a session, or pass
`observe=True` to `hiraku.launch.launch` or to the MCP `launch_entrypoint`
tool. An observed launch runs the step as usual, and also records:

- **outputs**: the project files the launch created or changed, found by
  comparing every file's size and modification time before and after;
- **inputs**: the files that existed before and that a Python process opened
  for reading, reported by an audit hook, plus the paths the command names,
  such as the script;
- **the launch**: its `--dispatch` and `--commit` options and its call-time
  arguments.

Each step's latest successful observed launch is stored as
`hiraku/snakemake/observed/<record>__<entrypoint>.json`:

```json
{
  "record": "model",
  "entrypoint": "train",
  "options": [],
  "arguments": ["--input", "data.csv", "--output", "model.pkl"],
  "inputs": ["data.csv", "train.py"],
  "outputs": ["model.pkl"],
  "observed_at": "..."
}
```

### Run the rules built from them

Without `./Snakefile`, or with `--observed`, `hiraku snakemake run` writes
`hiraku/snakemake/Snakefile` with one rule per observed step of the workflow
record and the records below it, and runs it:

- each rule launches its step with the observed options and arguments, and
  declares the observed files;
- a step that reads another step's output depends on it, so the order follows
  the files;
- `rule all` asks for every file no other step reads;
- more than 50 files a step creates under one new directory, such as a
  downloaded dataset, are declared as that `directory()`.

When the steps are not below the workflow record, name the record above them:

```bash
hiraku snakemake run --record pipeline --scope my-task
```

To change a step's rule, launch the step again with `--observe`. The latest
launch of each record and entrypoint wins, so one entrypoint launched with two
argument sets, such as two seeds, becomes one rule; write a `Snakefile` to keep
both.

### What observation leaves out

| Case | What happens | If you need it |
| --- | --- | --- |
| A non-Python program reads a file its command does not name | Not an input | Pass the path as an argument, or write a `Snakefile` |
| A file several steps write, the record's tracking file (its CSV file or MLflow SQLite database), `mlruns/`, or `wandb/` | Not a step file | Nothing; Snakemake does not own these |
| A step that writes no file of its own | Its printed output becomes `hiraku/snakemake/logs/<rule>.log` | Give the script an output file |
| `.git`, virtual environments, caches, `hiraku/` | Never observed | Declare record files in a `Snakefile` |
| More than 100,000 files in the project | The launch is not observed | Write a `Snakefile` |
| A dispatcher that runs the command elsewhere, such as `worktree` | Nothing is observed | Observe a launch with the default dispatcher |

Observation metadata stays on your machine: `hiraku/snakemake/` ignores itself
in Git. The launched command can still change tracked files or create new ones.
Collaborators observe the steps by launching them, or you commit a `Snakefile` written with
`hiraku snakemake generate --observed`. Launches made by `hiraku snakemake run`
are never observed, since their files are already declared.

## A written Snakefile

### Write the rules

Start from the observed rules, or from a draft with one rule per entrypoint of
the record and the records below it:

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
`--force` overwrites an existing file, and `--scope` selects the observed
steps as for `run`. `hiraku snakemake run` accepts any `Snakefile`, and fills in
`record` and `entrypoint` for the rules whose `shell:` runs
`hiraku launch ENTRYPOINT --record RECORD`.

### Steps whose code writes no file of its own

Snakemake needs every step to produce a file it owns. The best one is written
by the step's code, so give the script an option such as `--output` and pass
`{output}` to it, as above. Some scripts only append a row to a shared file,
such as the project-root `runs.csv`. That file cannot be the output of several
steps, so capture the launch's printed output instead:

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

### Why a written Snakefile suits agents

Observation spares a person from writing rules. An agent working through the
Hiraku CLI or MCP server is better served by writing the `Snakefile` itself:

- **It has the facts already.** The records state each step's command and
  arguments (`hiraku record show`), the scripts' argument parsers name their
  files, and `generate` (or `generate --observed`) gives the rule skeletons.
  Writing the rules is a small, declarative edit.
- **The command stays discoverable.** A rule that calls `hiraku launch`
  selects an entrypoint defined on a record. Review its extra arguments and
  any `--config` values too: these can change how the step runs. Hiraku also
  accepts rules that run other shell commands.
- **You can preview the dependency graph.** `hiraku snakemake run --dry-run`
  records the planned jobs and detects missing inputs or cycles before
  executing the steps.
- **The result is evidence to check against.** Each run's `jobs` lists what
  every rule ran, read, and wrote, so the agent can compare the execution with
  its intent from `hiraku runs --json`.
- **It is reviewable and shared.** The `Snakefile` is one file in the
  repository: a person reviews the agent's change as a diff, and collaborators
  and CI run the same rules. Observations stay on one machine.
- **It expresses what observation cannot**: wildcards over records, inputs the
  code does not open but the order needs, non-Python steps, and record files
  under `hiraku/`.

## Run the workflow

```bash
hiraku snakemake run --record workflow
hiraku runs --record workflow --json
hiraku dashboard --record workflow
```

The command needs a `snakemake` executable on `PATH`: install Hiraku with the
`snakemake` extra, for example `uv tool install '.[snakemake]'` from the
checkout, or pass `--command uv --command-arg run --command-arg snakemake` to
run Snakemake in a project environment that provides it. `hiraku` must be on
`PATH` inside the rules.

| Option | Effect |
| --- | --- |
| `--dry-run` | Records the planned jobs as a `planned` run, without running them |
| `--config 'train=--epochs 2'` | Snakemake config; rules that add `config.get("train", "")` pass it to the entrypoint |
| `--snakefile PATH` | Uses another file |
| `--observed` | Uses the observed launches even when `./Snakefile` exists |
| `--scope RECORD` | Takes the observed steps from `RECORD` and the records below it |
| `--force`, `--forceall`, `--keep-going` | Passed to Snakemake |
| `TARGETS` | Rule names or files to build instead of `rule all` |

Snakemake runs synchronously and locally from the project root. Options that
change the working directory or hand execution to a scheduler, profile,
cluster, or remote backend are rejected.

Which steps run is Snakemake's decision:

- a step runs when its output is missing, or an input it needs has changed;
  for small files Snakemake compares content, so `touch` alone does not count;
- Snakemake remembers in `.snakemake/` the rule that last built each file, and
  reruns a file it built when that rule changes;
- Snakemake works back from the requested files: while a final file is up to
  date, a deleted intermediate file is not rebuilt.

Launched entrypoints get the usual `hiraku launch` attribution, plus
`HIRAKU_WORKFLOW_RUN_ID`. The training code can store that ID with its own
runs, for example as an MLflow tag, to link them to the workflow execution.

## Stored evidence

Each execution is one run of the workflow record in its CSV file. The row is
written as `started` before Snakemake runs and replaced when it finishes, so an
interrupted execution still leaves evidence. Rows and columns already in the
file are kept.

Keep the file under `hiraku/` rather than in the default project-root
`runs.csv`. The evidence then follows however the records are versioned: when
`hiraku/` is ignored by Git or kept in its own repository, recording an
execution leaves the code checkout clean, so the next execution and its steps
still report `git_state: clean`. A separate file also keeps the extra columns
away from scripts that append to `runs.csv` with a fixed header.

| Column | Value |
| --- | --- |
| `commit`, `timestamp`, `entrypoint` | The executed `HEAD`, the start time, and `snakemake` |
| `param.command` | The Snakemake command line |
| `param.snakefile_sha256` | The Snakefile content digest |
| `outcome` | `started`, `planned`, `succeeded`, `no-op`, `failed`, or `interrupted` |
| `ended`, `git_state` | The end time, and `clean`, `dirty`, or `unknown` |
| `returncode`, `diagnostic` | The exit code and the end of the output on failure |
| `jobs` | A JSON list with one entry per step |

In `hiraku runs --json`, the extra CSV columns appear under `raw`. The `jobs`
value is still a JSON-encoded string; decode it with `json.loads(run["raw"]["jobs"])`
when inspecting it in Python.

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
Snakemake reports a command only for files it has built itself. A job whose
files were never built, as in a planned run, or were built outside Snakemake,
for example by an observed launch, shows `command`, `record`, and `entrypoint`
as `null`; the dashboard then names only its rule. `--forceall` rebuilds every
file through Snakemake.

Only synchronous local execution is supported. Checkpoints and remote storage
are outside this integration's supported scope. A Snakefile's Python code can
execute while its dependency graph is being inspected, including in a dry run.
