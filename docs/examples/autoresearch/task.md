# Autoresearch

## Goal

The goal is simple: get the lowest `val_bpb`. The training script runs for a
fixed time budget of 5 minutes on a single GPU, so architectural and
hyperparameter changes are compared under the same budget. Lower is better.

## Experiment surface

Modify `train.py` — this is the only file you edit. Everything is fair game:
model architecture, optimizer, hyperparameters, training loop, batch size,
model size, etc.

Do not modify `prepare.py`. It is read-only. It contains the fixed evaluation,
data loading, tokenizer, and training constants (time budget, sequence length,
etc.). Do not install new packages or add dependencies. Do not modify the
evaluation harness: `evaluate_bpb` in `prepare.py` is the ground truth metric.
Do not edit `runs.csv`; the Hiraku dispatcher owns the run table.

## Evaluation criteria

VRAM is a soft constraint. Some increase is acceptable for meaningful
`val_bpb` gains, but it should not blow up dramatically.

All else being equal, simpler is better. A small improvement that adds ugly
complexity is not worth it. Conversely, removing something and getting equal or
better results is a great outcome — that is a simplification win. When
evaluating whether to keep a change, weigh the complexity cost against the
improvement magnitude. A 0.001 `val_bpb` improvement that adds 20 lines of hacky
code is probably not worth it. A 0.001 improvement from deleting code is
definitely worth keeping. An improvement of approximately zero with much
simpler code should also be kept.

The first run must establish the unmodified upstream baseline. Every subsequent
result must be compared against the best retained result from this task.
