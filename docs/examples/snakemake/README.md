# Track a Snakemake workflow

The `snakemake` example ships a small two-rule workflow that copies an input
file to an output file. It shows how Hiraku captures a workflow execution's
declared inputs and outputs, observed file states, result, and provenance.

## Initialize

```bash
mkdir my-workflow
cd my-workflow
git init
hiraku init --example snakemake
git add .
git commit -m "initial commit"
```

Review `hiraku status` to confirm that the project is initialized.

## Create a Snakemake record

A Snakemake run is attributed to a record whose tracking backend is
`snakemake`:

```bash
hiraku record create workflow --type experiment \
  --set "hypothesis=The workflow reproduces the greeting output." \
  --set 'tracking_backend={type: snakemake}'
```

`record create` activates the new record, so `hiraku snakemake run` can resolve
it by ID as well.

## Run the workflow

The workflow's `all` rule copies `data/message.txt` to `greeting.txt`. The
`snakemake` executable must be on `PATH`:

```bash
hiraku snakemake run --record workflow --snakefile Snakefile --cores 1
hiraku runs --record workflow
hiraku runs --record workflow --json
hiraku dashboard --record workflow
```

Each execution is stored as one JSON run under `hiraku/runs/snakemake/`, with
declared inputs and outputs, observed file states, requested targets, and the
result. See [Snakemake tracking](../../guides/tracking-snakemake.md) for the
full command reference and the limits of best-effort file capture.
