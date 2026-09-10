# autoresearch

This is an experiment to have the LLM do its own research.

## Experimentation

Each experiment runs on a single GPU. The training script runs for a **fixed
time budget of 5 minutes** (wall clock training time, excluding
startup/compilation).

**What you CAN do:**

- Modify `train.py` — this is the only file you edit. Everything is fair game:
  model architecture, optimizer, hyperparameters, training loop, batch size,
  model size, etc.

**What you CANNOT do:**

- Modify `prepare.py`. It is read-only. It contains the fixed evaluation, data
  loading, tokenizer, and training constants (time budget, sequence length,
  etc).
- Install new packages or add dependencies. You can only use what's already in
  `pyproject.toml`.
- Modify the evaluation harness. The `evaluate_bpb` function in `prepare.py` is
  the ground truth metric.
- Modify `runs.csv`. The Hiraku dispatcher owns the run table.

**The goal is simple: get the lowest val_bpb.** Since the time budget is fixed,
you don't need to worry about training time — it's always 5 minutes. Everything
is fair game: change the architecture, the optimizer, the hyperparameters, the
batch size, the model size. The only constraint is that the code runs without
crashing and finishes within the time budget.

**VRAM** is a soft constraint. Some increase is acceptable for meaningful
val_bpb gains, but it should not blow up dramatically.

**Simplicity criterion**: All else being equal, simpler is better. A small
improvement that adds ugly complexity is not worth it. Conversely, removing
something and getting equal or better results is a great outcome — that's a
simplification win. When evaluating whether to keep a change, weigh the
complexity cost against the improvement magnitude. A 0.001 val_bpb improvement
that adds 20 lines of hacky code? Probably not worth it. A 0.001 val_bpb
improvement from deleting code? Definitely keep. An improvement of ~0 but much
simpler code? Keep.
