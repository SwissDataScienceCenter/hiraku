# Heart Disease example

The project files copied by `hiraku init --example heart-disease` are stored in
`src/`. See `docs/examples/heart-disease/README.md` in the Hiraku repository for
the complete walkthrough.

Add `--results` to include prepared research records and synthetic tracked runs.

Initialize in an empty Git repository (or add `--ignore-git` to initialize
without Git):

```bash
uv run hiraku init --example heart-disease --results
```

The results overlay includes four records and nine runs in `runs.csv`.
These are synthetic dashboard fixtures, not measured model results.
Browsing them requires no training, credentials, or network access.

From that directory, launch the dashboard using the repository's uv environment:

```bash
uv run --project /path/to/hiraku --extra dashboard hiraku dashboard
```

Select `val/log_loss` for a running minimum, `val/accuracy` for a running maximum,
or `duration` for dots without a best line. Click a dot to select its record.
