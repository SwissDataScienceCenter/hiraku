# nanochat example

This example is a small, Hiraku-ready adaptation of [Karpathy's nanochat](https://github.com/karpathy/nanochat).

## Create and set up the project

From the directory where you want the project:

```bash
hiraku example create nanochat --git
cd nanochat
```

Install the CPU or CUDA dependencies, then activate the environment:

```bash
uv sync --extra cpu   # CPU or Apple Silicon
# or
uv sync --extra gpu   # NVIDIA CUDA 12.8
source .venv/bin/activate
```

Training logs go to the project's local MLflow database. Set the tracking
environment for the commands below:

```bash
export MLFLOW_TRACKING_URI="sqlite:///$PWD/mlflow.db"
export MLFLOW_EXPERIMENT_NAME="hiraku-nanochat"
```

Hiraku will track each command launched with `hiraku run`. Data, tokenizer, and
model artifacts are cached under `~/.cache/nanochat` by default.

## CPU demo

This small run is for testing the pipeline, it downloads one training shard, trains a small tokenizer, and runs a short, shallow model. The first download is roughly 100 MB; the tokenizer
and model steps can still take a while on CPU.

```bash
hiraku run -- python -m dataset -n 1
hiraku run -- python -m tok_train --max-chars=2000000000
hiraku run -- python -m base_train \
    --depth=2 \
    --head-dim=64 \
    --window-pattern=L \
    --max-seq-len=512 \
    --device-batch-size=32 \
    --total-batch-size=16384 \
    --eval-every=100 \
    --eval-tokens=524288 \
    --core-metric-every=-1 \
    --sample-every=100 \
    --num-iterations=100 \
    --run-name=cpu-demo
```

## GPU run

This single-GPU example uses more data and a deeper model. Adjust
`--device-batch-size` down if the run runs out of GPU memory. Remove
`--device-type=cuda` to let nanochat choose the available device automatically.

```bash
hiraku run -- python -m dataset -n 8
hiraku run -- python -m tok_train --max-chars=2000000000
hiraku run -- python -m base_train \
    --device-type=cuda \
    --depth=8 \
    --head-dim=64 \
    --max-seq-len=512 \
    --window-pattern=L \
    --device-batch-size=4 \
    --total-batch-size=8192 \
    --eval-every=100 \
    --eval-tokens=16384 \
    --core-metric-every=-1 \
    --sample-every=100 \
    --num-iterations=1000 \
    --run-name=gpu-demo
```

Evaluate the most recent checkpoint with:

```bash
hiraku run -- python -m base_eval \
    --device-batch-size=1 \
    --split-tokens=16384 \
    --max-per-task=16
```

For upstream guidance on CPU/MPS sizing and multi-GPU training, see the
[nanochat README](https://github.com/karpathy/nanochat#running-on-cpu--mps).
