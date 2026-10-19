# Track Snakemake workflows

Configure a record to use Hiraku's local Snakemake run source:

```bash
hiraku record update workflow --set 'tracking_backend={type: snakemake}'
```

Run a workflow from the project directory:

```bash
hiraku snakemake run --record workflow --snakefile Snakefile --cores 2
hiraku runs --record workflow --json
hiraku dashboard --record workflow
```

The command requires a `snakemake` executable on `PATH`. It first performs a
`--dry-run --forceall` preview to capture job input and output declarations,
then executes the requested workflow. Use `--dry-run` on the Hiraku command to
save only a planned run. Target names can follow the options.

Each execution is stored as one JSON file under `hiraku/runs/snakemake/` by
default. A `tracking_backend` path override can select another directory.
The run includes record attribution, timestamp, result status, requested
targets, and local file observations. A failed execution is still recorded;
missing outputs retain a `missing` state. Input observations are taken before
execution and output observations afterward.

Snakemake's job log is a human readable format, so file capture is best effort.
Dynamic checkpoints, remote storage, and file names containing commas may not
be represented fully. The run metadata marks declarations as incomplete; it
must not be treated as an exhaustive provenance manifest. The command's
preview may also invoke Snakefile Python code, as any Snakemake dry run does.
