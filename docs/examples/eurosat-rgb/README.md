# EuroSAT RGB with Weights & Biases

![Preview of RGB satellite imagery from the EuroSAT dataset](https://huggingface.co/datasets/blanchon/EuroSAT_RGB/resolve/main/thumbnail.jpg)

*EuroSAT RGB contains 27,000 labeled 64×64 Sentinel-2 image patches across 10 land-cover classes. Source: [blanchon/EuroSAT_RGB](https://huggingface.co/datasets/blanchon/EuroSAT_RGB).*

This example trains a multilayer perceptron (MLP) and a convolutional neural network (CNN) on the EuroSAT RGB dataset and logs their training and validation metrics to Weights & Biases (W&B). Hiraku reads the resulting W&B runs through its tracking adapter.

## Setup

From a local Hiraku source checkout, install the Hiraku CLI:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install --force '.[wandb]'
```

Then create a project and let Hiraku install the training script, model definitions, and dependencies for this example.

```bash
hiraku example create eurosat-rgb --git
cd eurosat-rgb
uv sync
git add .
git commit -m "initialize EuroSAT RGB example"
```

The first training run downloads the RGB EuroSAT dataset automatically.

## Login to W&B

```bash
uv run wandb login
```

The example uses the `eurosat-rgb` W&B project, experiment-specific groups, and built-in tags by default.

Set `WANDB_ENTITY` only if you want to log under a specific team or account.

## Create the research root and child records

The example ships with W&B configured in `hiraku/leaderboards/default.yml`.
The leaderboard reads results; the training script configures its logging destination.

```bash
hiraku record create land-cover-classification \
  --title "Land cover classification" \
  --type task \
  --set "goal=Maximize EuroSAT RGB validation accuracy" \
  --set "question=How accurately can MLP and CNN architectures classify EuroSAT RGB land-cover images?"

hiraku record create land-cover-classification/mlp-baseline \
  --title "MLP baseline" \
  --parent land-cover-classification --type experiment \
  --set "summary=MLP baseline for EuroSAT RGB classification" \
  --set "hypothesis=A fully connected neural network can learn a useful baseline from flattened RGB image pixels." \
  --commit HEAD

hiraku record create land-cover-classification/cnn \
  --title "Convolutional network" \
  --parent land-cover-classification --type experiment \
  --set "summary=CNN baseline for EuroSAT RGB classification" \
  --set "hypothesis=A convolutional neural network improves classification accuracy over the MLP by learning spatial features from satellite imagery." \
  --commit HEAD

hiraku record list

hiraku job run --record land-cover-classification/mlp-baseline -- uv run python train.py --model mlp --seed 42 \
  --epochs 5 --batch-size 64 --learning-rate 0.001 --validation-fraction 0.2

hiraku job run --record land-cover-classification/cnn -- uv run python train.py --model cnn --seed 42 \
  --epochs 5 --batch-size 64 --learning-rate 0.001 --validation-fraction 0.2
```

## Compare final metrics

Inspect all runs associated with the research root:

```bash
hiraku leaderboard runs --sort val/accuracy
```
