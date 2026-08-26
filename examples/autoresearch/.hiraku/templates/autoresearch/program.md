# autoresearch

This is an experiment to have the LLM do its own research.

## Setup

To set up a new experiment, work with the user to:

1. **Get the current task**: run `hiraku task --json` and record its task ID as `<task>`. The task has already been created.
2. **Choose the history mode**: ask the user to choose exactly one of `revert`, `reset`, or `branch`. Use it for the entire task.
3. **Verify the task branch**: check that the current branch is `hiraku/task/<task>`. If it is not, switch to it with `git switch hiraku/task/<task>`.
4. **Read the in-scope files**: The repo is small. Read these files for full context:
   - `README.md` — repository context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader, evaluation. Do not modify.
   - `train.py` — the file you modify. Model architecture, optimizer, training loop.
5. **Verify data exists**: Check that `~/.cache/autoresearch/` contains data shards and a tokenizer. If not, tell the human to run `uv run prepare.py`.
6. **Verify run logging**: The training script prints its summary and writes each run directly to `tasks/<task>/runs.csv`. Do not edit the run table.
7. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment runs on a single GPU. The training script runs for a **fixed time budget of 5 minutes** (wall clock training time, excluding startup/compilation). You launch it as: `hiraku launch --experiment <experiment-id> <task>:gpu`.

**What you CAN do:**
- Modify `train.py` — this is the only file you edit. Everything is fair game: model architecture, optimizer, hyperparameters, training loop, batch size, model size, etc.

**What you CANNOT do:**
- Modify `prepare.py`. It is read-only. It contains the fixed evaluation, data loading, tokenizer, and training constants (time budget, sequence length, etc).
- Install new packages or add dependencies. You can only use what's already in `pyproject.toml`.
- Modify the evaluation harness. The `evaluate_bpb` function in `prepare.py` is the ground truth metric.
- Modify `tasks/<task>/runs.csv`. The training script owns the run table.

**The goal is simple: get the lowest val_bpb.** Since the time budget is fixed, you don't need to worry about training time — it's always 5 minutes. Everything is fair game: change the architecture, the optimizer, the hyperparameters, the batch size, the model size. The only constraint is that the code runs without crashing and finishes within the time budget.

**VRAM** is a soft constraint. Some increase is acceptable for meaningful val_bpb gains, but it should not blow up dramatically.

**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win. When evaluating whether to keep a change, weigh the complexity cost against the improvement magnitude. A 0.001 val_bpb improvement that adds 20 lines of hacky code? Probably not worth it. A 0.001 val_bpb improvement from deleting code? Definitely keep. An improvement of ~0 but much simpler code? Keep.

**The first run**: Your very first run should always be to establish the baseline, so you will run the training script as is. Create a `baseline` experiment using the chosen history mode and bind it to the current commit before launching it.

## Output format

Once the script finishes it prints a summary like this:

```
---
val_bpb:          0.997900
training_seconds: 300.1
total_seconds:    325.9
peak_vram_mb:     45060.2
mfu_percent:      39.80
total_tokens_M:   499.6
num_steps:        953
num_params_M:     50.3
depth:            8
```

Note that the script is configured to always stop after 5 minutes, so depending on the computing platform of this computer the numbers might look different. You can extract the key metric from the log file:

```
grep "^val_bpb:" run.log
```

## Logging results

When an experiment is done, the training script writes the run directly to `tasks/<task>/runs.csv`. Do not edit the run table.

Use `hiraku runs <task>` to inspect the results. Record whether the experiment was kept, discarded, or crashed by updating its experiment record with the appropriate status, decision, and short conclusion.

## The experiment loop

In `revert` and `reset` mode, experiments run on `hiraku/task/<task>`. In `branch` mode, each experiment runs on `hiraku/experiment/<task>/<experiment-id>`.

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on
2. Choose an experiment ID and create the experiment record. In `revert` or `reset` mode, use `hiraku exp create <task>:<experiment-id> --entrypoint gpu`. In `branch` mode, use `hiraku exp create <task>:<experiment-id> --branch --entrypoint gpu`.
3. Tune `train.py` with an experimental idea by directly hacking the code.
4. git commit, then bind the experiment with `hiraku exp set <task>:<experiment-id> --commit HEAD`
5. Run the experiment: `hiraku launch --experiment <experiment-id> <task>:gpu > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)
6. Read out the results: `grep "^val_bpb:\|^peak_vram_mb:" run.log`
7. If the grep output is empty, the run crashed. Run `tail -n 50 run.log` to read the Python stack trace and attempt a fix. If you can't get things to work after more than a few attempts, give up.
8. Inspect the run with `hiraku runs <task>` and update the experiment record. Do not edit the run table.
9. Apply the chosen history mode:
   - **revert**: If val_bpb improved (lower), keep the git commit. If val_bpb is equal or worse, run `git revert --no-edit <experiment-commit>`.
   - **reset**: If val_bpb improved (lower), keep the git commit. If val_bpb is equal or worse, git reset back to where you started.
   - **branch**: If val_bpb improved (lower), switch to `hiraku/task/<task>` and merge `hiraku/experiment/<task>/<experiment-id>`, keeping the experiment branch. If val_bpb is equal or worse, switch to `hiraku/task/<task>` without merging, keeping the experiment branch. Always return to the task branch before creating the next experiment.

The idea is that you are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. And you're advancing the task branch so that you can iterate. If you feel like you're getting stuck in some way, you can rewind but you should probably do this very very sparingly (if ever).

**Timeout**: Each experiment should take ~5 minutes total (+ a few seconds for startup and eval overhead). If a run exceeds 10 minutes, kill it and treat it as a failure (discard using the chosen history mode).

**Crashes**: If a run crashes (OOM, or a bug, or etc.), use your judgment: If it's something dumb and easy to fix (e.g. a typo, a missing import), fix it and re-run. If the idea itself is fundamentally broken, just skip it, record the crash in the experiment, apply the chosen history mode, and move on.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — read papers referenced in the code, re-read the in-scope files for new angles, try combining previous near-misses, try more radical architectural changes. The loop runs until the human interrupts you, period.

As an example use case, a user might leave you running while they sleep. If each experiment takes you ~5 minutes then you can run approx 12/hour, for a total of about 100 over the duration of the average human sleep. The user then wakes up to experimental results, all completed by you while they slept!
