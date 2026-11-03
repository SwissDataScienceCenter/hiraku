# Examples

## Minimal example

A minimal example to show how to initialize a project using Hiraku.

- [Start from scratch](minimal/README.md) — initialize a minimal project with a ready-to-run local evaluation script.

## Example projects

The examples are runnable projects that show the Hiraku lifecycle with different datasets and tracking backends.

- [Heart Disease](heart-disease/README.md) — the recommended first walkthrough. It ships with a small dataset and training script, and uses CSV tracking.
- [Iris with Weights & Biases](iris-wandb/README.md) — a small classification example using the Iris dataset and W&amp;B.
- [Digits with MLflow](digits-mlflow/README.md) — compare regularization strengths while logging learning curves to MLflow.
- [EuroSAT RGB](eurosat-rgb/README.md) — investigate image-specific architectural inductive biases with a flattened-image MLP baseline and follow-up experiments.

## Advanced examples

More advanced examples that show how to use Hiraku with LangGraph and autoresearch loops.

- [Workflow examples](workflows/README.md) — LangGraph workflows for a harness handoff and a record scheduler, plus a prompt-only workflow with arguments.
- [Autoresearch](autoresearch/README.md) — add a Hiraku research loop and tracked dispatcher to the original autoresearch repository.