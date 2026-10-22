# Examples

The examples are runnable projects that show the Hiraku lifecycle with different datasets and tracking backends.

- [Start from scratch](minimal/README.md) — initialize a minimal project with a ready-to-run local evaluation script.
- [Heart Disease](heart-disease/README.md) — the recommended first walkthrough. It ships with a small dataset and training script, and uses CSV tracking.
- [Iris with Weights & Biases](iris-wandb/README.md) — a small classification example using the Iris dataset and W&amp;B.
- [Digits with MLflow](digits-mlflow/README.md) — compare regularization strengths while logging learning curves to MLflow.
- [Digits with Snakemake](digits-snakemake/README.md) — run both regularization variants as a Snakemake DAG whose steps launch record entrypoints, while keeping MLflow measurements in separate experiment records.
- [EuroSAT RGB](eurosat-rgb/README.md) — investigate image-specific architectural inductive biases with a flattened-image MLP baseline and follow-up experiments.
- [Workflow examples](workflows/README.md) — a Python workflow that checks that a coding harness follows Hiraku workflow instructions and stops for the user, a prompt-only workflow with arguments, and a scheduler that picks its next step from records by priority.
- [Autoresearch](autoresearch/README.md) — add a Hiraku research loop and tracked dispatcher to the original autoresearch repository.
- [Snakemake pipelines](snakemake-pipelines/index.md) — run the steps of every example above as one Snakemake workflow, from observed launches or a written `Snakefile`, step by step.

??? note "Optional initialization flags"

    Heart Disease and Digits with MLflow support all three flags:

    - `--with-results` copies prepared records and measured runs, so you can
      browse results without training.

      ```bash
      hiraku init --example heart-disease --with-results
      ```

    - `--with-generator` copies `generate_results.py` into the new project so
      you can create fresh runs later.

      ```bash
      hiraku init --example heart-disease --with-generator
      ```

    - `--generate` creates fresh runs during initialization. It cannot be
      combined with `--with-results`; generation may take time and can require
      installing the example's dependencies.

      ```bash
      hiraku init --example heart-disease --generate
      ```

    See each example's README for details about its prepared results and
    generator.
