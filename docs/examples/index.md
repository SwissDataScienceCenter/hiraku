# Examples

The examples are runnable projects that show the Hiraku lifecycle with
different datasets and tracking backends.

- [Start from scratch](minimal/README.md) — initialize a minimal project with a ready-to-run local evaluation script.
- [Heart Disease](heart-disease/README.md) — the recommended first walkthrough. It ships with a small dataset and training script, and uses CSV tracking.
- [Iris with Weights & Biases](iris-wandb/README.md) — a small classification example using the Iris dataset and W&amp;B.
- [Digits with MLflow](digits-mlflow/README.md) — compare regularization strengths while logging learning curves to MLflow.
- [EuroSAT RGB](eurosat-rgb/README.md) — investigate image-specific architectural inductive biases with a flattened-image MLP baseline and follow-up experiments.
- [Workflow examples](workflow-examples/README.md) — a Python workflow that checks that a coding harness follows Hiraku workflow instructions and stops for the user, a prompt-only workflow with arguments, and a scheduler that picks its next step from records by priority.
- [Autoresearch](autoresearch/README.md) — add a Hiraku research loop and tracked dispatcher to the original autoresearch repository.
