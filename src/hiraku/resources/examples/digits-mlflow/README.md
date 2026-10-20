# Digits MLflow example

The project files copied by `hiraku clone digits-mlflow` are stored in
`src/`. See `docs/examples/digits-mlflow/README.md` in the Hiraku repository for
the complete setup and usage guide.

Initialize with `--with-results` to include three research records and six
measured MLflow runs. The bundled SQLite database contains metrics and their
epoch histories; it does not include model or plot artifacts. Artifact URI
fields are relative so the snapshot can move between project directories.
Use Hiraku's `mlflow` extra to query these results.

Generate fresh runs immediately with:

```bash
hiraku clone digits-mlflow . --generate
```

Or copy the generator for a separate step:

```bash
hiraku clone digits-mlflow . --with-generator
uv run --with hiraku python generate_results.py
```

From a checkout, run the copied script with
`uv run --project /path/to/hiraku python generate_results.py` instead.
Generation uses the example's dependency environment and local `mlflow.db`,
even if remote MLflow environment variables are set. No tracking server or
credentials are required. The first dependency installation may need network
access. Existing records or a database are refused.

Each regularization experiment runs seeds 1, 2 and 3 for 20 epochs with 64 hidden
units and a validation fraction of 0.25. Weak L2 uses alpha 0.0001; strong L2
uses 1.0. No statistical significance or improvement decision is claimed.
Run IDs, timestamps and runtime measurements vary between executions.

Maintainers can reproduce the committed results from the repository root:

```bash
uv run python src/hiraku/resources/examples/digits-mlflow/generate_results.py \
  --output /tmp/digits-results
```

The output directory must be new. The generator exports only configuration,
records and a SQLite backup after all six runs finish and pass validation.
It is not copied into initialized projects unless `--with-generator` is given.
