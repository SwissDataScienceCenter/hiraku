# EuroSAT RGB with Weights & Biases

![Preview of RGB satellite imagery from the EuroSAT dataset](https://huggingface.co/datasets/blanchon/EuroSAT_RGB/resolve/main/thumbnail.jpg)

*EuroSAT RGB contains 27,000 labeled 64×64 Sentinel-2 image patches across 10 land-cover classes. Source: [blanchon/EuroSAT_RGB](https://huggingface.co/datasets/blanchon/EuroSAT_RGB).*

This example trains a multilayer perceptron (MLP) and a convolutional neural network (CNN) on the EuroSAT RGB dataset and logs their training and validation metrics to Weights & Biases (W&B). Hiraku reads the resulting W&B runs through its tracking adapter.

## 1. Install Hiraku and initialize the example

From a local Hiraku source checkout, install the Hiraku CLI:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install '.[wandb]'
```

Then create a project and let Hiraku install the training script, model definitions, and dependencies for this example.

```bash
mkdir eurosat-rgb
cd eurosat-rgb
git init

hiraku clone eurosat-rgb .
uv sync

git add .
git commit -m "initialize EuroSAT RGB example"
```

The first training run downloads the RGB EuroSAT dataset automatically.

## 2. Login to W&B

```bash
uv run wandb login
```

The example uses the `eurosat-rgb` W&B project, experiment-specific groups, and built-in tags by default.

Set `WANDB_ENTITY` only if you want to log under a specific team or account.

## 3. Define the research root

Create a research root configured with the W&B backend:

```bash
hiraku record create eurosat --type task \
  --set "goal=Maximize EuroSAT RGB validation accuracy" \
  --set "question=How accurately can MLP and CNN architectures classify EuroSAT RGB land-cover images?" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}, {name: val/loss, direction: minimize}]' \
  --set 'tracking_backend={type: wandb, project: eurosat-rgb}'

hiraku record show eurosat
hiraku status
```

The research goal is to compare the ability of fully connected and convolutional architectures to classify satellite imagery under comparable training conditions.

## 4. Create an MLP baseline experiment

Create an experiment using a MLP with fully connected layers.

```bash
hiraku record create mlp --parent eurosat --type experiment \
  --set "summary=MLP baseline for EuroSAT RGB classification" \
  --set "hypothesis=A fully connected neural network can learn a useful baseline from flattened RGB image pixels." \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: mlp, seed: 42, epochs: 10, batch-size: 64, learning-rate: 0.001, validation-fraction: 0.2}}}'

hiraku record show mlp
hiraku record entrypoints mlp

hiraku launch train --record mlp --dry-run
hiraku launch train --record mlp

hiraku runs --record eurosat --all
hiraku runs --record mlp
```

The MLP flattens the input RGB image into a vector and applies fully connected layers to predict one of the 10 land-cover classes.

During training, the script logs training and validation loss, accuracy, and model parameter count to W&B.

## 5. Run a CNN experiment

Create a second experiment using a CNN.

```bash
hiraku record create cnn --parent eurosat --type experiment \
  --set "summary=CNN baseline for EuroSAT RGB classification" \
  --set "hypothesis=A convolutional neural network improves classification accuracy over the MLP by learning spatial features from satellite imagery." \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: cnn, seed: 42, epochs: 10, batch-size: 64, learning-rate: 0.001, validation-fraction: 0.2}}}'

hiraku record show cnn
hiraku record entrypoints cnn

hiraku launch train --record cnn
hiraku runs --record eurosat --all --sort val/accuracy
```

The CNN uses convolutional layers and spatial pooling to extract image features before producing class predictions.

## 6. Compare the experiments

Inspect all runs associated with the research root:

```bash
hiraku runs --record eurosat --all --sort val/accuracy
```

Each run stores Hiraku provenance, model and dataset configuration, random seed, training hyperparameters, epoch-level metrics, model parameter count, and W&B metadata.
