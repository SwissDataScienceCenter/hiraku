# Examples

The examples are runnable projects that show the Hiraku lifecycle with
different datasets and tracking backends.

- [Start from scratch](minimal/README.md) — initialize a minimal project with a ready-to-run local evaluation script.
- [Heart Disease](heart-disease/README.md) — the recommended first walkthrough. It ships with a small dataset and training script, and uses CSV tracking.
- [Iris with Weights & Biases](iris-wandb/README.md) — a small classification example using the Iris dataset and W&amp;B.
- [Digits with MLflow](digits-mlflow/README.md) — compare regularization strengths while logging learning curves to MLflow.
- [Autoresearch](autoresearch/README.md) — add a Hiraku research loop and tracked dispatcher to the original autoresearch repository.

To try any example in the interactive dashboard from a Hiraku source checkout,
run `uv sync --all-extras`, then choose one of the documented names:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --example minimal
uv run --all-extras python scripts/use_case_dashboard.py --example heart-disease
uv run --all-extras python scripts/use_case_dashboard.py --example digits-mlflow
uv run --all-extras python scripts/use_case_dashboard.py --example iris-wandb
uv run --all-extras python scripts/use_case_dashboard.py --example autoresearch
```

The helper prints the authenticated Hiraku URL, token, tracking location, and
retained demo directory. It opens the GUI and any local tracking UI. Use
`--reopen "<demo-path>"` to revisit a demo without creating or training it
again. Iris creates records without contacting W&B; use `--wandb-entity` and
`--run-iris` to log remote runs with your existing W&B login. The Autoresearch
demo exercises its Linux CPU mock; full training still requires the upstream
checkout and GPU described in its guide. Details and options are in the
[dashboard guide](../dashboard.md).
