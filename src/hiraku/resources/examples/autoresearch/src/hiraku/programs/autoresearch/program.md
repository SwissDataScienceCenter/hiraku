---
id: autoresearch
---
# autoresearch

## Setup

The Hiraku task and its dedicated branch have already been created. To set up
or resume an experiment, work with the user to:

1. **Get the current task**: Run `hiraku status --json` and
   `hiraku task show --json`. Record its task ID as `<task>` and its branch as
   `<task-branch>`. Read the complete canonical task record returned by Hiraku.
2. **Verify the task branch**: Check that the current branch is
   `<task-branch>`. If it is not, switch to it with
   `git switch <task-branch>`.
3. **Read the research history**: Run
   `hiraku experiment list --task <task> --json` and
   `hiraku run list --task <task> --json`. If an experiment is active or
   incomplete, inspect it with
   `hiraku experiment show <experiment-id> --task <task> --json` and inspect
   its attributed runs before doing new work.
4. **Read the in-scope files**: The repo is small. Read these files for full
   context:
   - `README.md` — repository context.
   - `prepare.py` — fixed constants, data prep, tokenizer, dataloader,
     evaluation. Do not modify.
   - `train.py` — the file you modify. Model architecture, optimizer, training
     loop.
5. **Verify data exists**: Check that `~/.cache/autoresearch/` contains data
   shards and a tokenizer. If not, tell the human to run `uv run prepare.py`.
6. **Resume safely**: If a bound incomplete experiment points at the current
   commit, finish its missing launch or decision before starting another. If a
   run already exists, do not repeat it. If the records and Git state disagree,
   surface the ambiguity during confirmation instead of overwriting work.
7. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment runs on a single GPU. The training script runs for a **fixed
time budget of 5 minutes** (wall clock training time, excluding
startup/compilation). You launch it as:
`hiraku launch <experiment-id>:train`.

**The first run**: Your very first run should always be to establish the
baseline, so you will run the training script as is. If no successful baseline
exists, create a `baseline` experiment bound to the current commit, launch it,
inspect its attributed run, and record `decision: keep` with a conclusion that
cites the run ID, commit, val_bpb, and peak VRAM. If a successful baseline is
already recorded, do not run it again.

```bash
hiraku experiment create baseline --task <task> \
  --summary "Unmodified upstream baseline" \
  --hypothesis "The upstream implementation establishes the reference val_bpb" \
  --commit HEAD
hiraku launch baseline:train > run.log 2>&1
hiraku run list --task <task> --experiment baseline --json
```

## Logging results

The Hiraku dispatcher writes every execution to project-root `runs.csv`. Do not
edit the run table. Inspect one experiment's parsed result and attribution with:

```bash
hiraku run list --task <task> --experiment <experiment-id> --json
```

For every completed attempt, update its experiment record with exactly one of
`decision: keep`, `decision: discard`, or `decision: crash`. Add a concise
conclusion citing the run ID, source commit, val_bpb, peak VRAM, and any relevant
failure evidence. Execution status in `runs.csv` is evidence; it is not the
research decision.

```bash
hiraku experiment update <experiment-id> --task <task> \
  --set decision=<decision> --set 'conclusion="<evidence-backed conclusion>"'
```

## The experiment loop

The experiment runs on the dedicated task branch recorded by Hiraku.

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on. Review the task's
   experiment records and runs, and identify the best retained result.
2. Choose an experiment ID and a single experimental idea. Tune `train.py` by
   directly hacking the code.
3. Commit only `train.py`. Each experiment represents exactly one candidate
   commit.
4. Create the experiment record with a short summary and hypothesis, binding it
   once with `hiraku experiment create <experiment-id> --task <task>
   --summary "<summary>" --hypothesis "<hypothesis>" --commit HEAD`.
   Never rebind an experiment to another commit.
5. Run the experiment:
   `hiraku launch <experiment-id>:train > run.log 2>&1` (redirect everything —
   do NOT use tee or let output flood your context).
6. Inspect the result with
   `hiraku run list --task <task> --experiment <experiment-id> --json`.
7. If the recorded execution crashed or timed out, run `tail -n 50 run.log` to
   read the Python stack trace or final diagnostics. If the dispatcher did not
   record a row, inspect the log and treat the missing result as a failure.
8. Update the experiment record with its decision and evidence-backed
   conclusion. Do this before changing Git history.
9. If val_bpb improved (lower), you "advance" the branch, keeping the git
   commit. If val_bpb is equal or worse, run
   `git reset --hard <experiment-commit>^` to go back to where you started.

The idea is that you are a completely autonomous researcher trying things out.
If they work, keep. If they don't, discard. And you're advancing the branch so
that you can iterate. If you feel like you're getting stuck in some way, you can
rewind but you should probably do this very very sparingly (if ever).

**Timeout**: Each experiment should take ~5 minutes total (+ a few seconds for
startup and eval overhead). If a run exceeds 10 minutes, kill it, record
`decision: crash` with the timeout evidence, and reset to the candidate commit's
parent.

**Crashes**: If a run crashes (OOM, or a bug, or etc.), use your judgment. First
record `decision: crash` and the evidence on the bound experiment. If it's
something dumb and easy to fix (e.g. a typo, a missing import), run
`git reset --soft <experiment-commit>^` to remove the crashed candidate while
preserving its changes, fix it, and commit it as a new attempt with a new
experiment record. Never rebind the crashed experiment. If the idea itself is
fundamentally broken, run `git reset --hard <experiment-commit>^` and move on.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do
NOT pause to ask the human if you should continue. Do NOT ask "should I keep
going?" or "is this a good stopping point?". The human might be asleep, or gone
from a computer and expects you to continue working *indefinitely* until you are
manually stopped. You are autonomous. If you run out of ideas, think harder —
read papers referenced in the code, re-read the in-scope files for new angles,
try combining previous near-misses, try more radical architectural changes. The
loop runs until the human interrupts you, period.

As an example use case, a user might leave you running while they sleep. If each
experiment takes you ~5 minutes then you can run approx 12/hour, for a total of
about 100 over the duration of the average human sleep. The user then wakes up
to experimental results, all completed by you while they slept!
