# Examples

The examples are runnable projects that show the Hiraku lifecycle with different datasets and tracking backends.

- [Start from scratch](minimal/README.md) — initialize a minimal project with a ready-to-run local evaluation script.
- [Heart Disease](heart-disease/README.md) — the recommended first walkthrough. It ships with a small dataset and training script, and uses CSV tracking.
- [Iris with Weights & Biases](iris-wandb/README.md) — a small classification example using the Iris dataset and W&amp;B.
- [Digits with MLflow](digits-mlflow/README.md) — compare regularization strengths while logging learning curves to MLflow.
- [EuroSAT RGB](eurosat-rgb/README.md) — investigate image-specific architectural inductive biases with a flattened-image MLP baseline and follow-up experiments.
- [Workflow examples](workflows/README.md) — LangGraph workflows for a harness handoff and a record scheduler, plus a prompt-only workflow with arguments.
- [Autoresearch](autoresearch/README.md) — add a Hiraku research loop and tracked dispatcher to the original autoresearch repository.

??? note "Optional initialization flags"

    Heart Disease and Digits with MLflow support all three flags:

    - `--with-results` copies prepared records and measured runs, so you can
      browse results without training.

      ```bash
      hiraku example create heart-disease --with-results
      ```

    - `--with-generator` copies `generate_results.py` into the new project so
      you can create fresh runs later.

      ```bash
      hiraku example create heart-disease --with-generator
      ```

    - `--generate` creates fresh runs during initialization. It cannot be
      combined with `--with-results`; generation may take time and can require
      installing the example's dependencies.

      ```bash
      hiraku example create heart-disease --generate
      ```

    See each example's README for details about its prepared results and
    generator.
