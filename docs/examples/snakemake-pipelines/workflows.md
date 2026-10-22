# Scheduler records as a Snakemake pipeline

The [scheduler workflow](../workflows/README.md#scheduler) creates a task with
experiment records that each carry a `priority` and a `count`. This page
snapshots each record to a JSON file and combines the snapshots into a report.
The steps pass files to each other, so the order comes from the files.

| Rule | Record · entrypoint | Reads | Writes |
| --- | --- | --- | --- |
| `scheduler_demo_1__snapshot` | `scheduler-demo-1` · `snapshot` | `scripts/snapshot.py` | `reports/scheduler-demo-1.json` |
| `scheduler_demo_2__snapshot` | `scheduler-demo-2` · `snapshot` | `scripts/snapshot.py` | `reports/scheduler-demo-2.json` |
| `scheduler_demo_3__snapshot` | `scheduler-demo-3` · `snapshot` | `scripts/snapshot.py` | `reports/scheduler-demo-3.json` |
| `scheduler_demo__report` | `scheduler-demo` · `report` | the three snapshots, `scripts/report.py` | `reports/summary.md` |

## 1. Create the project with the scheduler's records

```bash
mkdir scheduler-pipeline
cd scheduler-pipeline
git init
hiraku init --example workflows
hiraku record create scheduler-demo --type task \
  --set "summary=Scheduler example: raise each count to 2"
for spec in "1 2" "2 6" "3 4"; do
  read -r index priority <<<"$spec"
  hiraku record create "scheduler-demo-$index" --parent scheduler-demo \
    --type experiment --set "priority=$priority" --set count=2
done
git add .
git commit -m "initialize workflows example with scheduler records"
```

Running the scheduler workflow with an agent creates records like these. Here
they are created directly: the task `scheduler-demo` and three experiments
with priorities 2, 6, and 4. `priority` and `count` are ordinary record fields.

## 2. Add the scripts, the entrypoints, and the workflow record

```bash
mkdir -p scripts
cat > scripts/snapshot.py <<'EOF'
"""Write the launching record's priority and count as JSON."""

import argparse, json, os, subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
shown = subprocess.run(
    ["hiraku", "record", "show", os.environ["HIRAKU_RECORD_ID"], "--json"],
    capture_output=True, text=True, check=True,
)
values = json.loads(shown.stdout)["values"]
snapshot = {key: values.get(key) for key in ("id", "priority", "count")}
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(snapshot, indent=1) + "\n")
print(json.dumps(snapshot))
EOF
cat > scripts/report.py <<'EOF'
"""Combine record snapshots into a Markdown table ordered by priority."""

import argparse, json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--snapshots", required=True, help="Comma-separated JSON files.")
args = parser.parse_args()
paths = args.snapshots.split(",")
rows = sorted((json.loads(Path(p).read_text()) for p in paths), key=lambda r: -r["priority"])
lines = ["| record | priority | count |", "| --- | ---: | ---: |"]
lines += [f"| {r['id']} | {r['priority']} | {r['count']} |" for r in rows]
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text("\n".join(lines) + "\n")
print("\n".join(lines))
EOF
hiraku record update scheduler-demo \
  --set 'entrypoints={snapshot: {command: [python3, scripts/snapshot.py]}, report: {command: [python3, scripts/report.py]}}'
hiraku record create pipeline --parent scheduler-demo --type task \
  --set "summary=Snapshot the scheduler records and report them" \
  --set 'tracking_backend={type: csv, path: hiraku/runs.csv}'
printf '%s\n' '/.snakemake/' '/reports/' '__pycache__/' >> .gitignore
git add .
git commit -m "add the snapshot and report steps"
```

`snapshot.py` reads the launching record with `hiraku record show --json`,
using the `HIRAKU_RECORD_ID` that every `hiraku launch` sets.
`report.py` reads the snapshot files it is given. Both entrypoints are on the
task, and the experiments inherit `snapshot`, so
`hiraku launch snapshot --record scheduler-demo-1` snapshots that experiment.

## Path A: observe the steps

### 3. Launch each step once with `--observe`

```bash
for index in 1 2 3; do
  hiraku launch snapshot --record "scheduler-demo-$index" --observe -- \
    --output "reports/scheduler-demo-$index.json"
done
hiraku launch report --record scheduler-demo --observe -- --output reports/summary.md \
  --snapshots reports/scheduler-demo-1.json,reports/scheduler-demo-2.json,reports/scheduler-demo-3.json
cat reports/summary.md
cat hiraku/snakemake/observed/scheduler-demo__report.json
```

Each snapshot writes its JSON file, and the report orders the three records by
priority. The report's observation links it to the snapshots: they are files
it opened for reading.

```json
{
  "record": "scheduler-demo",
  "entrypoint": "report",
  "options": [],
  "arguments": ["--output", "reports/summary.md", "--snapshots", "reports/scheduler-demo-1.json,..."],
  "inputs": [
    "reports/scheduler-demo-1.json",
    "reports/scheduler-demo-2.json",
    "reports/scheduler-demo-3.json",
    "scripts/report.py"
  ],
  "outputs": ["reports/summary.md"],
  "observed_at": "..."
}
```

Each snapshot's inputs are `scripts/snapshot.py` and `hiraku.yml`, which the
`hiraku record show` it runs reads to find the project. The record file the
snapshot reports on is not among them, because files under `hiraku/` are never
observed.

### 4. Run the workflow from the observations

```bash
hiraku snakemake run --record pipeline --scope scheduler-demo
cat hiraku/snakemake/Snakefile
```

The generated rules state the order through the files: the report needs the
three snapshots, and `rule all` asks only for `reports/summary.md`, the one
file no other step reads. Every output already exists from step 3 and is up
to date, so nothing runs: `no-op`.

### 5. Remove two files and run again

```bash
rm reports/scheduler-demo-2.json reports/summary.md
hiraku snakemake run --record pipeline --scope scheduler-demo
```

Snakemake works back from the file `rule all` asks for. `reports/summary.md`
is missing, so the report must run; it needs `reports/scheduler-demo-2.json`,
so that snapshot runs first. The other two snapshots are up to date and do not
run. The workflow run is `succeeded`, and its steps show which two ran.
Removing only `reports/scheduler-demo-2.json` would be a `no-op`: while
`reports/summary.md` is up to date, Snakemake does not rebuild the files that
went into it.

## Path B: write the Snakefile

### 6. Write the rules

```bash
cat > Snakefile <<'EOF'
# Snapshot each scheduler record, then report them by priority.

RECORDS = ["scheduler-demo-1", "scheduler-demo-2", "scheduler-demo-3"]

rule all:
    input: "reports/summary.md"

rule snapshot:
    input: "scripts/snapshot.py", "hiraku/records/scheduler-demo/{record}.md"
    output: "reports/{record}.json"
    shell: "hiraku launch snapshot --record {wildcards.record} -- --output {output}"

rule scheduler_demo__report:
    input: "scripts/report.py", expand("reports/{record}.json", record=RECORDS)
    output: "reports/summary.md"
    shell:
        "hiraku launch report --record scheduler-demo -- --output {output} "
        "--snapshots $(echo {input[1]} {input[2]} {input[3]} | tr ' ' ,)"
EOF
git add Snakefile
git commit -m "add the scheduler Snakefile"
```

One rule with the wildcard `{record}` snapshots any record:
Snakemake fills it in from each file the report needs. This is a rule
observation cannot produce. The snapshot rule also declares the record's own
file as an input, so editing a record's `priority` reruns its snapshot and the
report.

### 7. Check the rules, then run them

```bash
hiraku snakemake run --record pipeline --dry-run
hiraku snakemake run --record pipeline
hiraku record update scheduler-demo-1 --set priority=9
hiraku snakemake run --record pipeline
cat reports/summary.md
```

The dry run is `planned`. The first run takes `scheduler-demo-2`'s snapshot
and the report (`succeeded`). Snakemake remembers, in `.snakemake/`, the rule
that last built each file. Step 5 built that snapshot with the observed rule,
and its rule has changed, so it runs again; the report follows because its
input changed. The other two snapshots came from the manual launches in step 3,
which Snakemake never recorded, so they stay.

`hiraku record update` then rewrites `scheduler-demo-1`'s record file, an input
of its snapshot, so the last run takes that snapshot and the report
(`succeeded`), and `scheduler-demo-1` moves to the top of the report.

## 8. Inspect the results

```bash
hiraku runs --record pipeline
hiraku dashboard
```

In the dashboard, select a workflow run to see its four steps. **Files** shows
each snapshot used by the report, and the record files used by their
snapshots. A snapshot that Snakemake has rebuilt shows its record, such as
`scheduler-demo-1` · `snapshot`. `reports/scheduler-demo-3.json` shows only
`rule snapshot`: the manual launch in step 3 built it, and Snakemake reports
the command behind a file only once it has built the file itself.

## What differs between the paths

| | Path A | Path B |
| --- | --- | --- |
| Snapshot rules | One per record, as observed | One rule with a `{record}` wildcard |
| Snapshot inputs | `scripts/snapshot.py`, `hiraku.yml` | `scripts/snapshot.py` and the record's file |
| Editing a record's `priority` | Does not rerun its snapshot | Reruns its snapshot and the report |
