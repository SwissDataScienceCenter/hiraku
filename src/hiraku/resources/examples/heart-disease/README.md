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

From that directory, launch the dashboard. It runs in Hiraku's environment, so
install Hiraku with the dashboard extra first
(`uv tool install --force '.[dashboard]'` from a Hiraku checkout):

```bash
hiraku dashboard
```

Select `val/log_loss` for a running minimum or `val/accuracy` for a running maximum.
Click a dot to select its record.
