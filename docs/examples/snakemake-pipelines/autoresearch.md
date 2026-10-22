# Autoresearch as a Snakemake pipeline

This page adds a data step and a training step to the
[autoresearch example](../autoresearch/README.md) and runs them as a workflow.
It uses the example's CPU mock in place of GPU training, so it runs on any
Linux machine. It shows two things: choosing a step's dispatcher, and an order
that only a written rule can state.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `sep10__prepare` | `sep10` · `prepare` | `prepare_mock.py` | `data/shards.txt` |
| `baseline__mock` | `baseline` · `mock` | `cpu_mock.py` | a row of `runs.csv` |

## 1. Create the project

```bash
git clone https://github.com/karpathy/autoresearch.git autoresearch-pipeline
cd autoresearch-pipeline
uv sync
hiraku init --example autoresearch
cat >> .gitignore <<'EOF'

# Hiraku local files and run artifacts
/.agents/
/.claude/
/.codex/
/.mcp.json
/hiraku/*
/hiraku.yml
/task.md
/runs.csv
/run.log

# Snakemake state and the pipeline's files
/.snakemake/
/data/
/logs/
EOF
git add .
git commit -m "add Hiraku autoresearch workflow"
```

This is the example's setup. The upstream `uv run prepare.py` and
`uv run train.py` are not needed: this page never trains on a GPU.
`hiraku init` installs the example's `autoresearch.sh` dispatcher as the
project default. It runs a command under a time limit, reads `val_bpb` from
its output, and appends a run to `runs.csv`, marked `crashed` when no `val_bpb`
appears. The `.gitignore` keeps `hiraku/`, the runs, and the pipeline's files
out of Git.

## 2. Add the records, the data step, and the workflow record

```bash
cat > prepare_mock.py <<'EOF'
"""CPU-only stand-in for prepare.py: writes a small token-shard manifest."""

import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--shards", type=int, default=4)
args = parser.parse_args()
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("".join(f"shard_{index:05d}.bin\n" for index in range(args.shards)))
print(f"Wrote {path}: {args.shards} mock token shards")
EOF
hiraku record create sep10 --type task \
  --set 'branch=autoresearch/sep10' \
  --set "goal=Minimize val_bpb within the fixed training budget" \
  --set 'metrics=[{name: val_bpb, direction: minimize}]' \
  --set 'entrypoints={train: {command: [uv, run, python, train.py]}, mock: {command: [uv, run, python, cpu_mock.py], args: {val-bpb: 1.0}}, prepare: {command: [uv, run, python, prepare_mock.py], args: {shards: 4}}}'
git switch -c autoresearch/sep10
git add prepare_mock.py
git commit -m "add mock data preparation"
hiraku record create baseline --parent sep10 --type experiment \
  --set "summary=Establish the baseline" \
  --commit HEAD
hiraku record create pipeline --parent sep10 --type task \
  --set "summary=Prepare data and train the baseline" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
```

`sep10` is the example's task with one more entrypoint, `prepare`, which runs
`prepare_mock.py`, a stand-in for the upstream data download. `baseline`
inherits `mock`, which runs `cpu_mock.py`: it prints a summary in the training
script's format, with `val_bpb: 1.0`, in a second. `pipeline` keeps the
workflow runs apart from the dispatcher's `runs.csv`.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
hiraku launch prepare --record sep10 --dispatch launch.sh --observe -- --output data/shards.txt
hiraku launch mock --record baseline --observe
cat hiraku/snakemake/observed/sep10__prepare.json
```

`prepare_mock.py` prints no `val_bpb`, so the default `autoresearch.sh` would
record it as `crashed`. `--dispatch launch.sh` runs it with Hiraku's plain
dispatcher instead. The observation keeps that choice, with the arguments:

```json
{
  "record": "sep10",
  "entrypoint": "prepare",
  "options": ["--dispatch", "launch.sh"],
  "arguments": ["--output", "data/shards.txt"],
  "inputs": ["prepare_mock.py"],
  "outputs": ["data/shards.txt"],
  "observed_at": "..."
}
```

The mock runs through `autoresearch.sh` and adds a `finished` run with
`val_bpb` 1.0 to `runs.csv`. Its only input is `cpu_mock.py`, and it writes no
file of its own.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope sep10
cat hiraku/snakemake/Snakefile
```

The generated rules are independent:

```python
rule baseline__mock:
    input:
        "cpu_mock.py",
    output:
        "hiraku/snakemake/logs/baseline__mock.log",
    shell:
        "hiraku launch mock --record baseline -- " + config.get('mock', '') + " | tee {output}"


rule sep10__prepare:
    input:
        "prepare_mock.py",
    output:
        "data/shards.txt",
    shell:
        "hiraku launch prepare --record sep10 --dispatch launch.sh -- --output data/shards.txt " + config.get('prepare', '')
```

The mock never reads `data/shards.txt`, so observation finds no order between
the steps. `data/shards.txt` already exists, so only the mock runs: one more
`finished` run in `runs.csv`, and a `succeeded` workflow run.

### 5. Run it again

```bash
hiraku snakemake run --record pipeline --scope sep10
```

Nothing changed: `no-op`.

## Path B: write the Snakefile

### 6. Write the rules, with the order you intend

```bash
cat > Snakefile <<'EOF'
# Prepare data, then train the baseline.
# Data preparation prints no val_bpb, so it uses the plain launch.sh dispatcher.

rule all:
    input: "logs/baseline.log"

rule sep10__prepare:
    input: "prepare_mock.py"
    output: "data/shards.txt"
    shell: "hiraku launch prepare --record sep10 --dispatch launch.sh -- --output {output}"

rule baseline__mock:
    input: "data/shards.txt", "train.py"
    output: "logs/baseline.log"
    shell: "hiraku launch mock --record baseline > {output}"
EOF
git add Snakefile
git commit -m "add the pipeline Snakefile"
```

The training rule declares two inputs the mock never opens. `data/shards.txt`
states that training comes after data preparation, as real training would
read the shards. `train.py` states that a change to the training code should
rerun training, which is what autoresearch experiments do. Observation can
only report what the code did; a written rule states what you intend.

### 7. Check the rules, run them, then change `train.py`

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline
sed -i 's/^DEPTH = 8 /DEPTH = 10/' train.py
git commit -am "Increase DEPTH to 10"
hiraku snakemake run --record pipeline
```

The dry run is `planned`. The first run trains the baseline once, creating
`logs/baseline.log` (`succeeded`). After the change to `train.py`, the next run
trains again, because `train.py` is an input of the training rule. Under
Path A, the same change would be a `no-op`.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku runs --record sep10 --all --sort val_bpb --ascending
hiraku dashboard --record sep10
```

The second command lists every mock training run with its `val_bpb`. In the
dashboard, the Path B workflow runs list both steps, and **Files** shows
`data/shards.txt` used by `baseline` · `mock`. Its producer appears as
`rule sep10__prepare`, without a record: Snakemake reports the command behind
a file only once it has built the file itself, and the observed launch in
step 3 built this one. After `hiraku snakemake run --record pipeline
--forceall`, it shows `sep10` · `prepare`.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Order | None: the mock reads no data | `prepare` before `mock`, as declared |
| Changing `train.py` | Does not rerun the mock | Reruns the mock |
| The dispatcher of `prepare` | `--dispatch launch.sh`, as observed | `--dispatch launch.sh`, as written |
